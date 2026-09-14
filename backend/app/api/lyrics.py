"""Lyrics status endpoint (§44-48, Phase 7). Deliberately minimal: a
lyrics-editing/review UI is out of v1 scope per the architecture doc — this
just surfaces enough (kind, provider, sidecar path) for a future UI to show
whether a track has lyrics and where they live.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.lyrics import Lyrics, LyricsKind
from app.db.session import get_session

router = APIRouter(prefix="/api/lyrics", tags=["lyrics"])


class LyricsOut(BaseModel):
    track_id: int
    provider: str
    kind: LyricsKind
    content: str | None
    sidecar_path: str | None

    @classmethod
    def from_model(cls, lyrics: Lyrics) -> "LyricsOut":
        return cls(
            track_id=lyrics.track_id,
            provider=lyrics.provider,
            kind=lyrics.kind,
            content=lyrics.content,
            sidecar_path=lyrics.sidecar_path,
        )


@router.get("/{track_id}", response_model=LyricsOut)
async def get_lyrics_for_track(track_id: int, session: AsyncSession = Depends(get_session)) -> LyricsOut:
    lyrics = await session.scalar(select(Lyrics).where(Lyrics.track_id == track_id))
    if lyrics is None:
        raise HTTPException(status_code=404, detail=f"No lyrics record for track {track_id}")
    return LyricsOut.from_model(lyrics)
