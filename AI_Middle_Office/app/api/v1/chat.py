import base64
import hashlib
import hmac
import re
import os
import csv
import uuid
from io import StringIO
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, status, UploadFile, File, Form, Body
from fastapi.responses import StreamingResponse
import asyncio
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
import requests
import jwt
import json
from pydantic import BaseModel

from app.core.database import get_db
from app.models.user import User
from app.models.quote_history import QuoteHistory
from app.core.security import SECRET_KEY, ALGORITHM

router = APIRouter()

# ==========================================
# 🚀 基础配置区
# ==========================================
N8N_WEBHOOK_URL_CALC = "http://192.168.88.128:5678/webhook/budget-calc"
N8N_WEBHOOK_URL_PUSH = "http://192.168.88.128:5678/webhook/budget-push"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
RAG_SERVICE_URL = os.environ.get("RAG_SERVICE_URL", "http://192.168.88.128:8001")
RELOAD_SECRET = os.environ.get("RELOAD_SECRET", "")


def _sign_payload(body: dict) -> dict:
    """返回带 HMAC-SHA256 签名的请求头"""
    body_bytes = json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    sig = hmac.new(WEBHOOK_SECRET.encode('utf-8'), body_bytes, hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json", "X-Webhook-Signature": f"sha256={sig}"}


# ==========================================
# 🛠️ 辅助功能与中间件
# ==========================================
def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="认证失效", headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None: raise credentials_exception
    except Exception:
        raise credentials_exception
    user = db.query(User).filter(User.username == username).first()
    if user is None: raise credentials_exception
    return user


async def analyze_image_with_domestic_ai(base64_image: str, mime_type: str):
    """利用国内智谱大模型 (GLM-4V) 提取业务信息"""
    if "请在这里" in ZHIPU_API_KEY or re.search(r'[\u4e00-\u9fa5]', ZHIPU_API_KEY):
        raise ValueError("API Key 格式异常(包含中文字符)")

    prompt = """你是一个专业的装修造价数据提取员。请严格提取这张图片表格中的【所有】行的完整信息。
⚠️ 提取红线：每一行必须完整包含【施工空间】、【施工项目】、【规格/工艺/材料要求】、【预估工程量】（面积/长度）四项数据。
⚠️ 分隔符生死线（极度重要）：
1. 每一行的所有信息自然合并为一句话，这句话【内部绝对不允许】出现分号（请统一用逗号替代）。
2. 【仅在】换行到下一个独立项目时，强制使用全角分号“；”作为分割。
示范格式：客厅直线型吊顶，使用龙牌轻钢龙骨无造型平顶要求做L型抗裂及接缝处理，50平米；厕所铲墙皮，铲除原大白腻子，50平米。
请直接输出结果，严禁包含任何Markdown格式或多余解释。"""

    url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {ZHIPU_API_KEY.strip()}"}
    payload = {
        "model": "glm-4v-flash",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {
                "url": f"data:{mime_type};base64,{base64_image}"}}]}
        ]
    }

    last_error = "未知错误"
    for delay in [1, 2]:
        try:
            response = await asyncio.to_thread(requests.post, url, headers=headers, json=payload, timeout=20)
            if response.status_code == 200:
                return response.json().get('choices', [{}])[0].get('message', {}).get('content', '')
            else:
                last_error = f"HTTP {response.status_code}: {response.text[:300]}"
                await asyncio.sleep(delay)
        except Exception as e:
            last_error = str(e)
            await asyncio.sleep(delay)

    raise RuntimeError(last_error)


# ==========================================
# 🌐 V2.0 业务核心接口 (聊天、算价、推送)
# ==========================================
@router.post("/chat", summary="AI 业务对话 (支持文本/图片/文档)")
async def process_chat(
        message: str = Form(None),
        file: UploadFile = File(None),
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    if current_user.quota <= 0:
        raise HTTPException(status_code=403, detail="您的 AI 调用额度已耗尽，请联系管理员充值")

    file_content = await file.read() if file else None
    mime_type = file.content_type if file else None
    filename = file.filename if file else None

    async def event_generator():
        yield f": {' ' * 1024}\n\n"
        final_query = message or ""
        try:
            yield f"data: {json.dumps({'status': 'processing', 'message': '[API Gateway] 📡 安全握手成功，已剥离 JWT 并唤醒底层引擎...'})}\n\n"
            await asyncio.sleep(0.5)

            if file_content:
                if "pdf" in mime_type.lower():
                    yield f"data: {json.dumps({'status': 'error', 'message': '❌ [格式校验] 拦截：国内引擎暂只支持图片输入，请截图重试'})}\n\n"
                    return

                yield f"data: {json.dumps({'status': 'processing', 'message': f'[Vision Module] 📸 正在驱动 GLM-4V 多模态大模型扫描附件: {filename}...'})}\n\n"

                base64_data = base64.b64encode(file_content).decode('utf-8')
                try:
                    extracted_text = await analyze_image_with_domestic_ai(base64_data, mime_type)
                except Exception as glm_err:
                    yield f"data: {json.dumps({'status': 'error', 'message': f'❌ [Vision Module] GLM-4V 调用失败: {str(glm_err)}'})}\n\n"
                    return

                if extracted_text:
                    final_query = f"{final_query} [从文件识别到的内容]: {extracted_text}".replace('\n', '；').replace(
                        '\r', '')
                    yield f"data: {json.dumps({'status': 'processing', 'message': '[Vision Module] ✅ 提取完毕，已成功结构化二维图纸特征！'})}\n\n"
                    await asyncio.sleep(0.5)
                else:
                    yield f"data: {json.dumps({'status': 'error', 'message': '❌ [Vision Module] GLM-4V 返回空内容，请重试'})}\n\n"
                    return
            else:
                if final_query.strip():
                    yield f"data: {json.dumps({'status': 'processing', 'message': '[Text Module] 📝 识别纯文本指令，正在执行语义清洗...'})}\n\n"
                    await asyncio.sleep(0.5)

            if not final_query.strip():
                yield f"data: {json.dumps({'status': 'error', 'message': '❌ 请输入业务指令或上传清单图片'})}\n\n"
                return

            yield f"data: {json.dumps({'status': 'processing', 'message': '[RAG & Agent] 🔍 正在穿透企业知识库寻找刚性底价并驱动专家大脑...\n(后台算力执行中，预计静候 15~30 秒，完成后将弹出核对面板)'})}\n\n"

            payload = {"text": {"content": final_query}, "conversationId": str(uuid.uuid4())}
            response = await asyncio.to_thread(requests.post, N8N_WEBHOOK_URL_CALC, json=payload, headers=_sign_payload(payload), timeout=180)

            if response.status_code == 200:
                try:
                    calc_result = response.json()
                except Exception:
                    body_preview = response.text[:500].strip() if response.text else "<empty>"
                    yield f"data: {json.dumps({'status': 'error', 'message': f'❌ [n8n Workflow] 响应体解析失败（HTTP 200）\n实际返回内容：{body_preview}\n→ 请确认 N8N budget-calc 工作流末尾有 Respond to Webhook 节点且 Response Body 设为 JSON'})}\n\n"
                    return
                current_user.quota -= 1
                db.commit()
                yield f"data: {json.dumps({'status': 'preview', 'message': '[n8n Workflow] ✅ AI 预审数据已就绪，请人工复核！', 'data': calc_result})}\n\n"
            else:
                try:
                    error_detail = response.json().get("message", "未知错误")
                except Exception:
                    error_detail = f"状态码 {response.status_code}，响应体为空"
                yield f"data: {json.dumps({'status': 'error', 'message': f'❌ [n8n Workflow] 中断: 底层算价引擎抛出异常 -> {error_detail}'})}\n\n"

        except requests.exceptions.Timeout:
            yield f"data: {json.dumps({'status': 'error', 'message': '❌ [n8n Workflow] 严重超时：请检查 Dify 模型是否拥堵挂起'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'message': f'❌ [API Gateway] 流转崩溃: {str(e)}'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
    )


@router.post("/confirm_push", summary="人工审核通过后推送钉钉")
async def confirm_and_push(
        payload: dict = Body(...),
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    try:
        response = requests.post(N8N_WEBHOOK_URL_PUSH, json=payload, headers=_sign_payload(payload), timeout=60)
        if response.status_code == 200:
            # 写入历史记录
            try:
                details = payload.get("project_details", [])
                total = sum(float(item.get("total_price", 0)) for item in details)
                record = QuoteHistory(
                    username=current_user.username,
                    total_amount=round(total, 2),
                    item_count=len(details),
                    payload_json=json.dumps(payload, ensure_ascii=False),
                )
                db.add(record)
                db.commit()
            except Exception:
                pass  # 历史写入失败不影响主流程
            return {"message": "✅ 最终报价单已成功投递至钉钉群！"}
        else:
            raise HTTPException(status_code=500, detail="底层推送流水线异常")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# 📊 V3.0 管理员后台 API (物料库增删改查与同步)
# ==========================================
# 数据模型：增加 is_draft 字段，用于标记新导入的待审数据
class MaterialItem(BaseModel):
    id: str
    item_name: str
    unit_price: float
    unit: str
    notes: str
    is_draft: Optional[bool] = False


DATA_FILE = "rag_materials.json"


def load_data():
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def require_admin(current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="权限不足，仅管理员可操作")
    return current_user


@router.get("/admin/materials", summary="获取所有物料清单")
async def get_materials(current_user: User = Depends(require_admin)):
    return {"code": 200, "data": load_data()}


@router.post("/admin/materials", summary="保存/覆盖整个物料清单")
async def save_materials(items: list[MaterialItem], current_user: User = Depends(require_admin)):
    data = [item.dict() if hasattr(item, 'dict') else item.model_dump() for item in items]
    save_data(data)
    return {"code": 200, "message": "保存成功"}


# 🚀🚀 新增功能：CSV 历史记录提炼引擎
@router.post("/admin/upload_csv", summary="解析并提炼历史成交记录")
async def upload_csv_history(file: UploadFile = File(...), current_user: User = Depends(require_admin)):
    """接收新的 CSV 成交记录，过滤重复项，提炼出异动/新项目"""
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="目前仅支持 CSV 格式历史记录导入")

    content = await file.read()

    # 🚀 防爆盾 1：拦截“假 CSV”（检测 Excel 文件的 PK 压缩包头）
    if content.startswith(b'PK'):
        raise HTTPException(status_code=400,
                            detail="解析拦截：检测到您直接修改了 Excel 文件的后缀名。请在 Excel 中打开文件，点击【文件 -> 另存为 -> CSV (逗号分隔)】后再上传！")

    # 兼容多种常见的中文编码
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            text = content.decode('gbk')
        except UnicodeDecodeError:
            text = content.decode('utf-8', errors='ignore')

    try:
        # 🚀 防爆盾 2：加入 newline='' 防止真实的 CSV 内部包含换行符导致解析崩溃
        reader = csv.DictReader(StringIO(text, newline=''))
        rows = list(reader)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"CSV 格式异常或损坏: {str(e)}")

    existing_data = load_data()
    existing_items = {str(item['item_name']).strip(): float(item['unit_price']) for item in existing_data}

    new_drafts = []

    for row in rows:
        # 🚀 兼容补充：加入了你表格里的专属表头 “AI核准单价(元)”
        item_name = row.get('施工项目') or row.get('项目名称') or row.get('item_name') or ""
        unit_price_str = row.get('AI核准单价(元)') or row.get('综合单价(元)') or row.get('单价') or row.get(
            'unit_price') or "0"
        unit = row.get('单位') or row.get('unit') or "项"
        notes = row.get('规格/工艺/材料说明') or row.get('备注说明') or row.get('备注') or row.get('notes') or ""

        item_name = str(item_name).strip()
        # 过滤汇总噪音和空行
        if not item_name or "汇总" in item_name or "结算" in item_name:
            continue

        try:
            price_val = float(unit_price_str)
        except ValueError:
            price_val = 0.0

        is_new = False

        # 核心逻辑 1：如果标准库里没有这个项目 -> 全新工艺，拉出待审
        if item_name not in existing_items:
            is_new = True
        else:
            # 核心逻辑 2：如果标准库里有，但这笔成交价格偏差超过 20% -> 异动待审
            old_price = existing_items[item_name]
            if old_price > 0 and abs(price_val - old_price) / old_price > 0.2:
                is_new = True
                item_name = f"{item_name} (历史异动待审)"

        if is_new:
            # 去重，防止同一个文件里的历史项目自己复读机
            if not any(d['item_name'] == item_name for d in new_drafts):
                new_drafts.append({
                    "id": f"draft_{uuid.uuid4().hex[:8]}",
                    "item_name": item_name,
                    "unit_price": price_val,
                    "unit": unit,
                    "notes": notes,
                    "is_draft": True  # 打上红色高亮草稿烙印
                })

    return {"code": 200, "message": "解析成功", "data": new_drafts}


@router.post("/admin/sync_milvus", summary="同步数据至 Milvus（委托 RAG 服务执行，零停机蓝绿切换）")
async def sync_to_milvus(current_user: User = Depends(require_admin)):
    """将本地 JSON 数据 POST 至 CentOS RAG 服务的 /admin/reload，由其完成向量化和蓝绿切换。
    Windows 端不再加载大模型，速度更快、内存占用更低。"""
    data = load_data()
    if not data:
        raise HTTPException(status_code=400, detail="本地知识库为空，无法同步")

    try:
        payload = {"materials": data, "secret": RELOAD_SECRET}
        response = await asyncio.to_thread(
            requests.post,
            f"{RAG_SERVICE_URL}/admin/reload",
            json=payload,
            timeout=120,
        )
        if response.status_code == 200:
            return {"code": 200, "message": response.json().get("message", "同步完成")}
        else:
            detail = response.json().get("detail", f"状态码 {response.status_code}")
            raise HTTPException(status_code=500, detail=f"RAG 服务返回错误: {detail}")
    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="RAG 服务超时，请检查 CentOS 容器状态")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# 👥 用户配额管理接口
# ==========================================
class QuotaUpdate(BaseModel):
    quota: int


@router.get("/admin/users", summary="获取所有用户列表")
async def list_users(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    users = db.query(User).order_by(User.id).all()
    return {"code": 200, "data": [
        {"id": u.id, "username": u.username, "role": u.role, "quota": u.quota, "is_active": u.is_active}
        for u in users
    ]}


# ==========================================
# 📋 报价历史记录接口
# ==========================================
@router.get("/history", summary="查询报价历史（本人；admin 可查全部）")
async def get_history(
    page: int = 1,
    page_size: int = 20,
    username: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    query = db.query(QuoteHistory)
    if current_user.role != "admin":
        query = query.filter(QuoteHistory.username == current_user.username)
    elif username:
        query = query.filter(QuoteHistory.username == username)

    total = query.count()
    records = query.order_by(QuoteHistory.created_at.desc()) \
                   .offset((page - 1) * page_size).limit(page_size).all()

    return {"code": 200, "total": total, "data": [
        {
            "id": r.id,
            "username": r.username,
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
            "total_amount": r.total_amount,
            "item_count": r.item_count,
            "payload_json": r.payload_json,
        } for r in records
    ]}


@router.patch("/admin/users/{user_id}/quota", summary="设置指定用户的 AI 调用额度")
async def set_user_quota(
    user_id: int,
    body: QuotaUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    if body.quota < 0:
        raise HTTPException(status_code=400, detail="额度不能为负数")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.quota = body.quota
    db.commit()
    return {"code": 200, "message": f"已将 {user.username} 的额度设置为 {body.quota} 次"}