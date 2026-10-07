from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class StreamStatus(str, enum.Enum):
    stopped = "stopped"
    running = "running"
    starting = "starting"
    error = "error"


class SegmentStatus(str, enum.Enum):
    recorded = "recorded"
    processed = "processed"
    error = "error"


class ReconstructionStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class FrameStatus(str, enum.Enum):
    captured = "captured"
    processed = "processed"
    error = "error"


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    rtsp_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    streams: Mapped[list[Stream]] = relationship(back_populates="camera", cascade="all, delete-orphan")
    zones: Mapped[list[Zone]] = relationship(back_populates="camera", cascade="all, delete-orphan")


class Stream(Base):
    __tablename__ = "streams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[StreamStatus] = mapped_column(Enum(StreamStatus), default=StreamStatus.stopped)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    camera: Mapped[Camera] = relationship(back_populates="streams")
    events: Mapped[list[FootfallEvent]] = relationship(back_populates="stream")


class Zone(Base):
    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    polygon: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_wait_time_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    camera: Mapped[Camera] = relationship(back_populates="zones")
    events: Mapped[list[FootfallEvent]] = relationship(back_populates="zone")


class FootfallEvent(Base):
    __tablename__ = "footfall_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    stream_id: Mapped[int | None] = mapped_column(ForeignKey("streams.id", ondelete="SET NULL"), nullable=True)
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id", ondelete="SET NULL"), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_type: Mapped[str] = mapped_column(String(20), default="entry", index=True)
    count: Mapped[int] = mapped_column(Integer, default=1)

    stream: Mapped[Stream | None] = relationship(back_populates="events")
    zone: Mapped[Zone | None] = relationship(back_populates="events")


class VideoSegment(Base):
    __tablename__ = "video_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    stream_id: Mapped[int | None] = mapped_column(ForeignKey("streams.id", ondelete="SET NULL"), nullable=True)
    path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_seconds: Mapped[float] = mapped_column(Float)
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[SegmentStatus] = mapped_column(Enum(SegmentStatus), default=SegmentStatus.recorded)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Frame(Base):
    __tablename__ = "frames"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    stream_id: Mapped[int | None] = mapped_column(ForeignKey("streams.id", ondelete="SET NULL"), nullable=True)
    path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[FrameStatus] = mapped_column(Enum(FrameStatus), default=FrameStatus.captured)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ReconstructionJob(Base):
    __tablename__ = "reconstruction_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[ReconstructionStatus] = mapped_column(
        Enum(ReconstructionStatus), default=ReconstructionStatus.pending
    )
    output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class RuntimeConfig(Base):
    __tablename__ = "runtime_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class AnalysisJobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class AnalysisJob(Base):
    """Job for running YOLO analysis on frames for a camera/time range."""

    __tablename__ = "analysis_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[AnalysisJobStatus] = mapped_column(
        Enum(AnalysisJobStatus), default=AnalysisJobStatus.pending
    )
    total_frames: Mapped[int] = mapped_column(Integer, default=0)
    processed_frames: Mapped[int] = mapped_column(Integer, default=0)
    unique_persons: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    tracks: Mapped[list["PersonTrack"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class PersonTrack(Base):
    """A unique person track across multiple frames."""

    __tablename__ = "person_tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("analysis_jobs.id", ondelete="CASCADE"), index=True)
    track_id: Mapped[int] = mapped_column(Integer, index=True)  # YOLO-assigned track ID
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id", ondelete="SET NULL"), nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    appearances: Mapped[int] = mapped_column(Integer, default=1)
    avg_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    dwell_seconds: Mapped[float] = mapped_column(Float, default=0.0)

    job: Mapped[AnalysisJob] = relationship(back_populates="tracks")
    detections: Mapped[list["PersonDetection"]] = relationship(back_populates="track", cascade="all, delete-orphan")


class PersonDetection(Base):
    """A single person detection in a specific frame."""

    __tablename__ = "person_detections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("person_tracks.id", ondelete="CASCADE"), index=True)
    frame_id: Mapped[int] = mapped_column(ForeignKey("frames.id", ondelete="CASCADE"), index=True)
    bbox_x1: Mapped[float] = mapped_column(Float)
    bbox_y1: Mapped[float] = mapped_column(Float)
    bbox_x2: Mapped[float] = mapped_column(Float)
    bbox_y2: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    in_zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id", ondelete="SET NULL"), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    track: Mapped[PersonTrack] = relationship(back_populates="detections")


class QueueJobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class QueueAnalysisJob(Base):
    """Job for running YOLO QueueManager analysis on frames for a zone."""

    __tablename__ = "queue_analysis_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id", ondelete="CASCADE"), index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[QueueJobStatus] = mapped_column(Enum(QueueJobStatus), default=QueueJobStatus.pending)
    total_frames: Mapped[int] = mapped_column(Integer, default=0)
    processed_frames: Mapped[int] = mapped_column(Integer, default=0)
    max_queue_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_queue_count: Mapped[float] = mapped_column(Float, default=0.0)
    avg_wait_time_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_throughput_exits_per_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    frame_results: Mapped[list["QueueFrameResult"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class QueueFrameResult(Base):
    """Queue count result for a single frame."""

    __tablename__ = "queue_frame_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("queue_analysis_jobs.id", ondelete="CASCADE"), index=True)
    frame_id: Mapped[int] = mapped_column(ForeignKey("frames.id", ondelete="CASCADE"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    queue_count: Mapped[int] = mapped_column(Integer)
    wait_time_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    throughput_exits_per_sec: Mapped[float | None] = mapped_column(Float, nullable=True)

    job: Mapped[QueueAnalysisJob] = relationship(back_populates="frame_results")


class FaceReference(Base):
    """Database of known faces and their embeddings for facial recognition."""

    __tablename__ = "face_references"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    image_path: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
