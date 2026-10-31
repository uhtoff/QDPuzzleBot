"""The shared archive-to-hub runner."""
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

from bot.core import hunt_archive, hub_archive_run
from bot.core.hub_archive_run import run_archive_to_hub


def _hunt():
    h = MagicMock()
    h.id = 10; h.name = "H"; h.role_id = 0; h.start_time = None; h.uid = "U1"
    return h


def _channel():
    ch = MagicMock(); ch.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    return ch


def _service(report):
    async def run(guild, hunt, invoker, hub, hub_row, *, on_progress=None):
        await on_progress(1, 2, "puz")
        return report
    svc = MagicMock(); svc.archive_hunt_to_hub = AsyncMock(side_effect=run)
    return svc


async def test_runner_locks_posts_progress_and_summary():
    report = hunt_archive.HuntArchiveReport(channels_archived=4, final_name="H - me", hub_channel_id=3000)
    status_ch = _channel()
    hub = MagicMock(); hub_dest = _channel(); hub.get_channel.return_value = hub_dest
    held = []

    @contextlib.asynccontextmanager
    async def lock(ids):
        held.append(list(ids)); yield

    with patch.object(hub_archive_run, "puzzles_lock", lock), \
         patch.object(hub_archive_run, "PuzzleJsonDb") as Db:
        Db.get_all_from_hunt.return_value = [MagicMock(id=3), MagicMock(id=1)]
        result = await run_archive_to_hub(MagicMock(), _service(report), MagicMock(), _hunt(),
                                          MagicMock(), hub, MagicMock(), status_channel=status_ch)
    assert result is report
    assert held == [[3, 1]]
    status_ch.send.assert_any_await("⏳ Archiving…")
    status_msg = status_ch.send.return_value
    status_msg.edit.assert_awaited_once_with(content="⏳ Archived 1/2 — puz")
    assert "H - me" in hub_dest.send.await_args.args[0]
    assert "H - me" in status_ch.send.await_args.args[0]


async def test_runner_falls_back_to_the_hub_archive_channel_for_status():
    report = hunt_archive.HuntArchiveReport(final_name="H - me", hub_channel_id=3000)
    hub = MagicMock(); archive_ch = _channel(); hub.get_channel.return_value = archive_ch
    guild = MagicMock(); guild.get_role.return_value = None
    invoker = MagicMock(); invoker.display_name = "me"
    with patch.object(hub_archive_run.hunt_archive, "ensure_hub_archive_channel",
                      new=AsyncMock(return_value=archive_ch)) as ensure, \
         patch.object(hub_archive_run, "PuzzleJsonDb") as Db:
        Db.get_all_from_hunt.return_value = []
        await run_archive_to_hub(MagicMock(), _service(report), guild, _hunt(), invoker,
                                 hub, MagicMock(), status_channel=None)
    ensure.assert_awaited_once()
    archive_ch.send.assert_any_await("⏳ Archiving…")
    # summary goes to it once, not twice (status channel is the hub channel)
    summaries = [c for c in archive_ch.send.await_args_list if "Archived hunt" in c.args[0]]
    assert len(summaries) == 1


async def test_runner_logs_and_returns_none_on_error():
    svc = MagicMock(); svc.archive_hunt_to_hub = AsyncMock(side_effect=RuntimeError("boom"))
    ch = _channel()
    with patch.object(hub_archive_run, "PuzzleJsonDb") as Db, \
         patch.object(hub_archive_run.logger, "exception") as log:
        Db.get_all_from_hunt.return_value = []
        result = await run_archive_to_hub(MagicMock(), svc, MagicMock(), _hunt(), MagicMock(),
                                          MagicMock(), MagicMock(), status_channel=ch)
    assert result is None
    log.assert_called()
    ch.send.return_value.edit.assert_awaited_once_with(
        content=":x: Archiving to the hub failed — the error has been logged. Re-run to finish.")


async def test_runner_failure_before_status_posts_to_channel():
    ch = _channel()
    with patch.object(hub_archive_run, "PuzzleJsonDb") as Db:
        Db.get_all_from_hunt.side_effect = RuntimeError("db")
        result = await run_archive_to_hub(MagicMock(), MagicMock(), MagicMock(), _hunt(), MagicMock(),
                                          MagicMock(), MagicMock(), status_channel=ch)
    assert result is None
    assert "failed" in ch.send.await_args.args[0]
