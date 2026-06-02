# BIZ-2t Cost Data Governance Summary

- Generated at: 2026-05-28T02:45:12+00:00
- Scope: cost_items all statuses + BIZ-2k active quality result
- Total cost items: 208
- Status counts: {"active": 195, "archived": 13}
- Action count: 126
- Risk counts: high=5 / medium=27 / low=94
- Trial blockers: 5
- Quote-used active items: 42

## Trial Readiness

- Recommendation: cleanup_before_trial
- Latest RAG sync ok: True
- Blockers:
  - High-risk governance actions remain.

## Latest RAG Sync

- id: 5
- status: success
- requested_count: 195
- synced_count: 195
- started_at: 2026-05-28T01:40:09
- message: 零停机热更新完成，共同步 195 条（quotation_blue -> enterprise_quotation_rag）
- error: None

## Priority Actions

| Risk | Issue type | Item | Action | Owner |
| --- | --- | --- | --- | --- |
| high | missing_named_reference_price | #203 临时静音保护棉铺设 | 请补齐至少一个业务可解释的来源价，便于后续演示报价依据。 | cost_department |
| high | missing_named_reference_price | #204 定制异形铝合金收口条安装 | 请补齐至少一个业务可解释的来源价，便于后续演示报价依据。 | cost_department |
| high | missing_named_reference_price | #206 高空局部防尘围挡加固 | 请补齐至少一个业务可解释的来源价，便于后续演示报价依据。 | cost_department |
| high | missing_named_reference_price | #207 高空局部防尘围挡加固 | 请补齐至少一个业务可解释的来源价，便于后续演示报价依据。 | cost_department |
| high | missing_named_reference_price | #208 甲方指定品牌成品检修口更换 | 请补齐至少一个业务可解释的来源价，便于后续演示报价依据。 | cost_department |
| medium | same_name_multi_spec | #46 夹板窗帘盒 | 演示时可用于展示同名不同规格切换；治理时请确认每条规格足够可辨认。 | cost_department |
| medium | missing_spec_on_multi_name | #128 304#不锈钢上下夹（短夹） | 请补充规格/特征，至少说明适用范围或工艺差异。 | cost_department |
| medium | missing_spec_on_multi_name | #131 304#不锈钢上下夹（长夹）-同门扇宽（800-900长） | 请补充规格/特征，至少说明适用范围或工艺差异。 | cost_department |
| medium | same_name_multi_spec | #206 高空局部防尘围挡加固 | 演示时可用于展示同名不同规格切换；治理时请确认每条规格足够可辨认。 | cost_department |
| medium | missing_spec | #135 玻璃门进口锁夹及安装 | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| medium | missing_spec | #165 满堂超高脚手架(≥4.2米) | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| medium | missing_spec | #166 单排钢脚手架（外墙装饰用） | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| medium | missing_spec | #172 拆除复合木地板 | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| medium | missing_spec | #173 拆除木脚线 | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| medium | missing_spec | #174 砖墙、砼墙面抹灰面铲除 | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| medium | missing_spec | #176 拆砖墙（120厚砖墙） | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| medium | missing_spec | #179 拆除石膏线（不分规格） | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| medium | missing_spec | #180 窗帘盒/灯槽拆除 | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| medium | missing_spec | #181 拆除轻钢龙骨石膏板、埃特板吊顶或轻钢龙骨铝扣板、铝塑板吊顶或轻钢龙骨木饰面吊顶 | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| medium | missing_spec | #185 拆除原有水箱座厕,清理,搬运 | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| medium | missing_spec | #188 拆除原有洗手盆,清理,搬运 | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| medium | missing_spec | #190 拆除原有冷热水龙头,清理 | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| medium | unit_needs_review | #206 高空局部防尘围挡加固 | 如属于 m/㎡/m³/kg/项/套/个等常见口径，请统一写法；特殊单位可保留。 | cost_department |
| medium | unit_needs_review | #207 高空局部防尘围挡加固 | 如属于 m/㎡/m³/kg/项/套/个等常见口径，请统一写法；特殊单位可保留。 | cost_department |
| medium | unit_needs_review | #208 甲方指定品牌成品检修口更换 | 如属于 m/㎡/m³/kg/项/套/个等常见口径，请统一写法；特殊单位可保留。 | cost_department |
| medium | similar_active_items | #35 轻钢龙骨石膏板平面天花          (不含乳胶漆)间距300*600 | 请人工确认是否为合理拆分，或后续需要合并/补充规格说明。 | cost_department |
| medium | similar_active_items | #35 轻钢龙骨石膏板平面天花          (不含乳胶漆)间距300*600 | 请人工确认是否为合理拆分，或后续需要合并/补充规格说明。 | cost_department |
| medium | similar_active_items | #46 夹板窗帘盒 | 请人工确认是否为合理拆分，或后续需要合并/补充规格说明。 | cost_department |
| medium | similar_active_items | #51 夹板底木饰面天花吊顶   （轻钢龙骨12mm夹板底，木饰面面层） | 请人工确认是否为合理拆分，或后续需要合并/补充规格说明。 | cost_department |
| medium | similar_active_items | #176 拆砖墙（120厚砖墙） | 请人工确认是否为合理拆分，或后续需要合并/补充规格说明。 | cost_department |
| medium | similar_active_items | #185 拆除原有水箱座厕,清理,搬运 | 请人工确认是否为合理拆分，或后续需要合并/补充规格说明。 | cost_department |
| medium | similar_active_items | #206 高空局部防尘围挡加固 | 请人工确认是否为合理拆分，或后续需要合并/补充规格说明。 | cost_department |
| low | same_name_multi_spec | #4 楼地面贴地面砖 | 演示时可用于展示同名不同规格切换；治理时请确认每条规格足够可辨认。 | cost_department |
| low | same_name_multi_spec | #48 弧形夹板窗帘盒 | 演示时可用于展示同名不同规格切换；治理时请确认每条规格足够可辨认。 | cost_department |
| low | same_name_multi_spec | #61 天花单向铝格栅吊顶（1.0mm） | 演示时可用于展示同名不同规格切换；治理时请确认每条规格足够可辨认。 | cost_department |
| low | same_name_multi_spec | #62 天花单向铝格栅吊顶（0.8mm） | 演示时可用于展示同名不同规格切换；治理时请确认每条规格足够可辨认。 | cost_department |
| low | same_name_multi_spec | #65 实木收口木饰面地脚线 | 演示时可用于展示同名不同规格切换；治理时请确认每条规格足够可辨认。 | cost_department |
| low | same_name_multi_spec | #96 不锈钢线条安装人工（含辅材） | 演示时可用于展示同名不同规格切换；治理时请确认每条规格足够可辨认。 | cost_department |
| low | same_name_multi_spec | #100 石材线条安装人工（含辅材） | 演示时可用于展示同名不同规格切换；治理时请确认每条规格足够可辨认。 | cost_department |
| low | same_name_multi_spec | #111 砌内墙灰砂砖 | 演示时可用于展示同名不同规格切换；治理时请确认每条规格足够可辨认。 | cost_department |
| low | same_name_multi_spec | #116 地弹簧 | 演示时可用于展示同名不同规格切换；治理时请确认每条规格足够可辨认。 | cost_department |
| low | same_name_multi_spec | #128 304#不锈钢上下夹（短夹） | 演示时可用于展示同名不同规格切换；治理时请确认每条规格足够可辨认。 | cost_department |
| low | same_name_multi_spec | #131 304#不锈钢上下夹（长夹）-同门扇宽（800-900长） | 演示时可用于展示同名不同规格切换；治理时请确认每条规格足够可辨认。 | cost_department |
| low | same_name_multi_spec | #149 墙面、天花肌理漆 | 演示时可用于展示同名不同规格切换；治理时请确认每条规格足够可辨认。 | cost_department |
| low | same_name_multi_spec | #167 双排钢管脚手架（零星工程价格） | 演示时可用于展示同名不同规格切换；治理时请确认每条规格足够可辨认。 | cost_department |
| low | missing_spec | #56 350mm高以内铝百叶出风口 | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| low | missing_spec | #128 304#不锈钢上下夹（短夹） | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| low | missing_spec | #131 304#不锈钢上下夹（长夹）-同门扇宽（800-900长） | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| low | missing_spec | #137 淋浴间安装直径22mm不锈钢管挂帘杆壁厚1.2mm厚 | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| low | missing_spec | #139 卫生间洗手台钢架制作（不小于40角钢制作） | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| low | missing_spec | #140 甲供银镜安装（重叠镜） | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| low | missing_spec | #169 装修综合脚手架 | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| low | missing_spec | #170 楼地面块料面层(瓷砖及石材类）及水泥砂浆结合层铲除 | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| low | missing_spec | #171 楼地面整体水泥砂浆面层及结合层铲除 | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| low | missing_spec | #175 铲除墙纸 | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| low | missing_spec | #177 拆砖墙（180厚砖墙） | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| low | missing_spec | #178 凿除墙面瓷砖、石材及水泥砂浆结合层、找平层 | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| low | missing_spec | #182 天花做轻钢龙骨石膏板吊顶（不换轻钢龙骨，只换面层石膏板） | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| low | missing_spec | #183 天花更换方形铝扣板（只换面层方形铝扣板） | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| low | missing_spec | #184 天花开灯孔（仅用于已有旧天花面上） | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| low | missing_spec | #186 拆除原有脚踏蹲厕,清理,搬运 | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| low | missing_spec | #187 拆除原有水箱蹲厕,清理,搬运 | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| low | missing_spec | #189 拆除原有冷水龙头,清理 | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| low | missing_spec | #191 已完工程及设备（成品）保护费 | 如该条目确实不区分规格可保留；否则建议补齐，便于报价依据解释。 | cost_department |
| low | unit_needs_review | #33 卫生间素砼防水槛 | 如属于 m/㎡/m³/kg/项/套/个等常见口径，请统一写法；特殊单位可保留。 | cost_department |
| low | unit_needs_review | #125 不锈钢拉手φ38*800MM | 如属于 m/㎡/m³/kg/项/套/个等常见口径，请统一写法；特殊单位可保留。 | cost_department |
| low | unit_needs_review | #126 不锈钢大拉手φ38*1200mm | 如属于 m/㎡/m³/kg/项/套/个等常见口径，请统一写法；特殊单位可保留。 | cost_department |
| low | unit_needs_review | #133 直角方管不锈钢大拉手 | 如属于 m/㎡/m³/kg/项/套/个等常见口径，请统一写法；特殊单位可保留。 | cost_department |
| low | unit_needs_review | #134 不锈钢拉手 | 如属于 m/㎡/m³/kg/项/套/个等常见口径，请统一写法；特殊单位可保留。 | cost_department |
| low | similar_active_items | #4 楼地面贴地面砖 | 请人工确认是否为合理拆分，或后续需要合并/补充规格说明。 | cost_department |
| low | similar_active_items | #16 地面工字铺贴实木木地板连9mm夹板基层及防潮(不含地面找平) | 请人工确认是否为合理拆分，或后续需要合并/补充规格说明。 | cost_department |
| low | similar_active_items | #16 地面工字铺贴实木木地板连9mm夹板基层及防潮(不含地面找平) | 请人工确认是否为合理拆分，或后续需要合并/补充规格说明。 | cost_department |
| low | similar_active_items | #17 地面斜铺贴实木木地板连9mm夹板基层及防潮(不含地面找平) | 请人工确认是否为合理拆分，或后续需要合并/补充规格说明。 | cost_department |
| low | similar_active_items | #25 成品瓷砖波打线 /门槛 （1、本子目仅计铺120mm～250mm宽(含250mm宽)波打线人工费用；
2、乙供瓷砖另计且粘贴费在瓷砖主材中考虑；
 3、宽超250mm以上瓷砖按楼地面瓷砖以面积计算） | 请人工确认是否为合理拆分，或后续需要合并/补充规格说明。 | cost_department |
| low | similar_active_items | #28 1：3水泥砂浆超厚每增加10mm | 请人工确认是否为合理拆分，或后续需要合并/补充规格说明。 | cost_department |
| low | similar_active_items | #36 轻钢龙骨防水石膏板平面天花 (不含乳胶漆)间距300*600 | 请人工确认是否为合理拆分，或后续需要合并/补充规格说明。 | cost_department |
| low | similar_active_items | #42 150高石膏角线 | 请人工确认是否为合理拆分，或后续需要合并/补充规格说明。 | cost_department |
| low | similar_active_items | #48 弧形夹板窗帘盒 | 请人工确认是否为合理拆分，或后续需要合并/补充规格说明。 | cost_department |
| low | similar_active_items | #55 天花成品木饰面安装 | 请人工确认是否为合理拆分，或后续需要合并/补充规格说明。 | cost_department |
| low | similar_active_items | #61 天花单向铝格栅吊顶（1.0mm） | 请人工确认是否为合理拆分，或后续需要合并/补充规格说明。 | cost_department |
