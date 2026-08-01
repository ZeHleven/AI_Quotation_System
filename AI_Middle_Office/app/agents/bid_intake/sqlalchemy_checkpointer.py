from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from sqlalchemy.orm import Session

from app.models.bid_intake_runtime import (
    BidIntakeCheckpoint,
    BidIntakeCheckpointBlob,
    BidIntakeCheckpointWrite,
)


class SqlAlchemyCheckpointSaver(BaseCheckpointSaver):
    """LangGraph checkpointer backed by the platform's SQLAlchemy database.

    It intentionally follows LangGraph's blob/checkpoint/write separation:
    channel values are versioned once, checkpoints reference those versions,
    and pending writes remain independently replayable after a process crash.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        serde=None,
    ) -> None:
        super().__init__(serde=serde)
        self._session_factory = session_factory

    @contextmanager
    def _session(self) -> Iterator[Session]:
        db = self._session_factory()
        try:
            yield db
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id, checkpoint_ns = _scope(config)
        checkpoint_id = get_checkpoint_id(config)
        with self._session() as db:
            query = db.query(BidIntakeCheckpoint).filter(
                BidIntakeCheckpoint.thread_id == thread_id,
                BidIntakeCheckpoint.checkpoint_ns == checkpoint_ns,
            )
            if checkpoint_id:
                row = query.filter(
                    BidIntakeCheckpoint.checkpoint_id == checkpoint_id
                ).one_or_none()
            else:
                row = query.order_by(
                    BidIntakeCheckpoint.checkpoint_id.desc()
                ).first()
            if row is None:
                return None
            return self._tuple_from_row(
                db,
                row,
                requested_config=config if checkpoint_id else None,
            )

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        before_id = get_checkpoint_id(before) if before else None
        requested_checkpoint_id = get_checkpoint_id(config) if config else None
        with self._session() as db:
            query = db.query(BidIntakeCheckpoint)
            if config:
                configurable = config.get("configurable", {})
                if thread_id := configurable.get("thread_id"):
                    query = query.filter(
                        BidIntakeCheckpoint.thread_id == str(thread_id)
                    )
                if "checkpoint_ns" in configurable:
                    query = query.filter(
                        BidIntakeCheckpoint.checkpoint_ns
                        == str(configurable.get("checkpoint_ns") or "")
                    )
            if requested_checkpoint_id:
                query = query.filter(
                    BidIntakeCheckpoint.checkpoint_id
                    == requested_checkpoint_id
                )
            if before_id:
                query = query.filter(
                    BidIntakeCheckpoint.checkpoint_id < before_id
                )
            rows = query.order_by(
                BidIntakeCheckpoint.checkpoint_id.desc()
            ).all()
            emitted = 0
            for row in rows:
                item = self._tuple_from_row(db, row, requested_config=None)
                if filter and not all(
                    item.metadata.get(key) == value
                    for key, value in filter.items()
                ):
                    continue
                if limit is not None and emitted >= limit:
                    break
                emitted += 1
                yield item

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id, checkpoint_ns = _scope(config)
        checkpoint_copy = checkpoint.copy()
        channel_values = checkpoint_copy.pop("channel_values")
        with self._session() as db:
            for channel, version in new_versions.items():
                identity = {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "channel": str(channel),
                    "version": str(version),
                }
                row = (
                    db.query(BidIntakeCheckpointBlob)
                    .filter_by(**identity)
                    .one_or_none()
                )
                if channel in channel_values:
                    value_type, value_blob = self.serde.dumps_typed(
                        channel_values[channel]
                    )
                else:
                    value_type, value_blob = "empty", b""
                if row is None:
                    db.add(
                        BidIntakeCheckpointBlob(
                            **identity,
                            value_type=value_type,
                            value_blob=value_blob,
                        )
                    )
                else:
                    row.value_type = value_type
                    row.value_blob = value_blob

            checkpoint_type, checkpoint_blob = self.serde.dumps_typed(
                checkpoint_copy
            )
            metadata_type, metadata_blob = self.serde.dumps_typed(
                get_checkpoint_metadata(config, metadata)
            )
            identity = {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": str(checkpoint["id"]),
            }
            row = (
                db.query(BidIntakeCheckpoint)
                .filter_by(**identity)
                .one_or_none()
            )
            values = {
                "parent_checkpoint_id": config.get("configurable", {}).get(
                    "checkpoint_id"
                ),
                "checkpoint_type": checkpoint_type,
                "checkpoint_blob": checkpoint_blob,
                "metadata_type": metadata_type,
                "metadata_blob": metadata_blob,
            }
            if row is None:
                db.add(BidIntakeCheckpoint(**identity, **values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            db.commit()
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": str(checkpoint["id"]),
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id, checkpoint_ns = _scope(config)
        checkpoint_id = str(
            config.get("configurable", {}).get("checkpoint_id") or ""
        )
        if not checkpoint_id:
            raise ValueError("checkpoint_id is required when persisting writes")
        with self._session() as db:
            for index, (channel, value) in enumerate(writes):
                write_index = WRITES_IDX_MAP.get(channel, index)
                identity = {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                    "task_id": str(task_id),
                    "write_index": int(write_index),
                }
                row = (
                    db.query(BidIntakeCheckpointWrite)
                    .filter_by(**identity)
                    .one_or_none()
                )
                if row is not None and write_index >= 0:
                    continue
                value_type, value_blob = self.serde.dumps_typed(value)
                values = {
                    "task_path": str(task_path or "")[:500],
                    "channel": str(channel),
                    "value_type": value_type,
                    "value_blob": value_blob,
                }
                if row is None:
                    db.add(BidIntakeCheckpointWrite(**identity, **values))
                else:
                    for key, stored_value in values.items():
                        setattr(row, key, stored_value)
            db.commit()

    def delete_thread(self, thread_id: str) -> None:
        with self._session() as db:
            for model in (
                BidIntakeCheckpointWrite,
                BidIntakeCheckpointBlob,
                BidIntakeCheckpoint,
            ):
                db.query(model).filter(model.thread_id == thread_id).delete(
                    synchronize_session=False
                )
            db.commit()

    async def aget_tuple(
        self,
        config: RunnableConfig,
    ) -> CheckpointTuple | None:
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        values = await asyncio.to_thread(
            lambda: list(
                self.list(
                    config,
                    filter=filter,
                    before=before,
                    limit=limit,
                )
            )
        )
        for value in values:
            yield value

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return await asyncio.to_thread(
            self.put,
            config,
            checkpoint,
            metadata,
            new_versions,
        )

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await asyncio.to_thread(
            self.put_writes,
            config,
            writes,
            task_id,
            task_path,
        )

    async def adelete_thread(self, thread_id: str) -> None:
        await asyncio.to_thread(self.delete_thread, thread_id)

    def _tuple_from_row(
        self,
        db: Session,
        row: BidIntakeCheckpoint,
        *,
        requested_config: RunnableConfig | None,
    ) -> CheckpointTuple:
        checkpoint = self.serde.loads_typed(
            (row.checkpoint_type, bytes(row.checkpoint_blob))
        )
        channel_versions = checkpoint.get("channel_versions", {})
        if channel_versions:
            identities = {
                (str(channel), str(version))
                for channel, version in channel_versions.items()
            }
            blobs = (
                db.query(BidIntakeCheckpointBlob)
                .filter(
                    BidIntakeCheckpointBlob.thread_id == row.thread_id,
                    BidIntakeCheckpointBlob.checkpoint_ns
                    == row.checkpoint_ns,
                )
                .all()
            )
            blob_map = {
                (item.channel, item.version): item for item in blobs
            }
            checkpoint["channel_values"] = {
                channel: self.serde.loads_typed(
                    (
                        blob_map[(str(channel), str(version))].value_type,
                        bytes(
                            blob_map[
                                (str(channel), str(version))
                            ].value_blob
                        ),
                    )
                )
                for channel, version in channel_versions.items()
                if (str(channel), str(version)) in identities
                and (str(channel), str(version)) in blob_map
                and blob_map[(str(channel), str(version))].value_type
                != "empty"
            }
        else:
            checkpoint["channel_values"] = {}

        writes = (
            db.query(BidIntakeCheckpointWrite)
            .filter(
                BidIntakeCheckpointWrite.thread_id == row.thread_id,
                BidIntakeCheckpointWrite.checkpoint_ns == row.checkpoint_ns,
                BidIntakeCheckpointWrite.checkpoint_id == row.checkpoint_id,
            )
            .order_by(
                BidIntakeCheckpointWrite.task_id.asc(),
                BidIntakeCheckpointWrite.write_index.asc(),
            )
            .all()
        )
        config = requested_config or {
            "configurable": {
                "thread_id": row.thread_id,
                "checkpoint_ns": row.checkpoint_ns,
                "checkpoint_id": row.checkpoint_id,
            }
        }
        return CheckpointTuple(
            config=config,
            checkpoint=checkpoint,
            metadata=self.serde.loads_typed(
                (row.metadata_type, bytes(row.metadata_blob))
            ),
            pending_writes=[
                (
                    item.task_id,
                    item.channel,
                    self.serde.loads_typed(
                        (item.value_type, bytes(item.value_blob))
                    ),
                )
                for item in writes
            ],
            parent_config=(
                {
                    "configurable": {
                        "thread_id": row.thread_id,
                        "checkpoint_ns": row.checkpoint_ns,
                        "checkpoint_id": row.parent_checkpoint_id,
                    }
                }
                if row.parent_checkpoint_id
                else None
            ),
        )


def _scope(config: RunnableConfig) -> tuple[str, str]:
    configurable = config.get("configurable", {})
    thread_id = str(configurable.get("thread_id") or "")
    if not thread_id:
        raise ValueError("thread_id is required for LangGraph persistence")
    checkpoint_ns = str(configurable.get("checkpoint_ns") or "")
    return thread_id, checkpoint_ns
