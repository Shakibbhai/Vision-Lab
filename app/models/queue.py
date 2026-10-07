from __future__ import annotations

from pydantic import BaseModel


class QueueAnalysisRequest(BaseModel):
    camera_id: int
    zone_id: int
    start: str
    end: str


class QueueJobResponse(BaseModel):
    id: int
    camera_id: int
    zone_id: int
    start_time: str
    end_time: str
    status: str
    total_frames: int
    processed_frames: int
    max_queue_count: int
    avg_queue_count: float
    avg_wait_time_sec: float | None = None
    avg_throughput_exits_per_sec: float | None = None
    error: str | None
    created_at: str


class QueueFrameResultResponse(BaseModel):
    timestamp: str
    queue_count: int
    wait_time_sec: float | None = None
    throughput_exits_per_sec: float | None = None


class QueueResultsResponse(BaseModel):
    job_id: int
    camera_id: int
    zone_id: int
    zone_name: str | None
    status: str
    max_queue_count: int
    avg_queue_count: float
    avg_line_length: float | None = None
    current_line_length: int | None = None
    avg_wait_time_sec: float | None = None
    avg_throughput_exits_per_sec: float | None = None
    frame_results: list[QueueFrameResultResponse]
