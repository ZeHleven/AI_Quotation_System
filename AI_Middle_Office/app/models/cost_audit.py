from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, func

from app.core.database import Base


class CostAccessAuditLog(Base):
    __tablename__ = "cost_access_audit_logs"
    __table_args__ = (
        Index("ix_cost_access_audit_logs_created_at", "created_at"),
        Index("ix_cost_access_audit_logs_action_created", "action", "created_at"),
        Index("ix_cost_access_audit_logs_user_created", "user_id", "created_at"),
        Index("ix_cost_access_audit_logs_resource", "resource_type", "resource_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(64), index=True, nullable=False)
    resource_type = Column(String(64), index=True, nullable=False)
    resource_id = Column(String(64), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    username = Column(String(64), index=True, nullable=True)
    roles_snapshot = Column(Text, nullable=True)
    request_path = Column(String(255), nullable=True)
    request_method = Column(String(16), nullable=True)
    client_ip = Column(String(64), nullable=True)
    user_agent = Column(Text, nullable=True)
    trace_id = Column(String(64), nullable=True)
    filters_json = Column(Text, nullable=True)
    result_count = Column(Integer, nullable=True)
    status = Column(String(24), index=True, nullable=False, default="success", server_default="success")
    message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
