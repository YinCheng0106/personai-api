# PK timer WebSocket contract

`/ws/pk?room=1234&exercise_type=squat` uses the existing verified
`["personai.v1", JWT]` subprotocol and Origin allowlist. Exactly two distinct
users with the same exercise can join. Video stays in the browser; the PK socket
only carries room state, timing and repetition scores.

## State and messages

The server owns `waiting -> countdown -> playing -> finished`.

| Message | Payload | Meaning |
| --- | --- | --- |
| `READY` (client) | `ready: boolean` | Only changes readiness while waiting. Both ready starts one timer. |
| `ROOM_STATE` | `player_count`, `ready`, `score`, `opponent_ready`, `opponent_score`, `exercise_type`, `phase`, timing fields | Per-player snapshot; waiting after an interrupted match cancels local countdown/analysis. |
| `GAME_START` | `countdown_seconds: 10`, `duration_seconds: 60`, timing fields | Preparation only; does not enable scoring. |
| `GAME_BEGIN` | `duration_seconds: 60`, timing fields | Enables confirmed ACTIVE repetitions until the deadline. |
| `SCORE_UPDATE` (client) | `score: integer` | Cumulative count, strictly the previous accepted count + 1, within 0..10000. |
| `OPPONENT_SCORE` | `score: integer` | Opponent's accepted cumulative count. |
| `GAME_END` | `score: integer`, `opponent_score: integer`, `result: win/lose/draw` | Immutable, per-player final result. |

Timing fields are numeric milliseconds: `server_time_ms`, `starts_at_ms`,
`ends_at_ms`. Start/end fields are epoch timestamps; the server snapshot follows
the same monotonic clock used to enforce the match, even if wall time changes.
The browser converts the server-relative remaining time to a `performance.now()`
deadline. A delayed/duplicate `GAME_BEGIN` never extends the deadline. Local zero
stops analysis and score sends, but final scores/results require `GAME_END`.
One-way transport delay can still make display clocks slightly different.
The server checks score eligibility under the room lock using monotonic time,
not either browser's display clock. A score queued before the deadline but only
validated at/after it is ignored. Timer wakeups recheck the deadline, so an early
wakeup cannot start or finish a match early. A delayed timer never extends play;
if the whole match window elapsed before countdown processing resumes, the server
sends the final result without an expired `GAME_BEGIN`.

Well-formed score updates in preparation or after expiration are ignored, so
in-flight frames cannot erase results by disconnecting a player. In-flight scores
after an interrupted match are also ignored until the next match. Fresh waiting-room
scores, malformed values, duplicate or skipped active scores remain protocol
errors (4400). Non-ACTIVE pose results never produce frontend score updates.
Readiness changes during countdown/playing/finished are ignored.

## Disconnect and cleanup

Leaving/disconnecting during countdown or play cancels the match (no forfeit or
fabricated result), clears scores/readiness and returns the survivor to waiting.
A player may explicitly rejoin after their old socket is removed; both must
prepare again. Duplicate concurrent connections for one user are rejected.
Finished results survive the opponent leaving. Finished rooms do not accept new
players; both players leave and create/join a fresh room for another match.
The last departure deletes the room. Failed/bounded socket sends use the same
cleanup path; one failed final notification cannot prevent the other's result.
Old socket cleanup cannot remove or control a replacement socket.

Room operations recheck the room object's identity after waiting for its lock.
Broadcasts stop when that room is no longer registered. Timer transitions also
check the owning task, so deleting/reusing a room code or starting another match
cannot let old cleanup or a stale timer modify the replacement. Sends retain the
existing five-second timeout per recipient. A failed `GAME_BEGIN` aborts the
match and stops its timer; failed `GAME_END` sends preserve the completed scores
and still attempt the other player's result. Finishing is idempotent and only
allowed once the server deadline has elapsed.

Cancelling an active timer or an unexpected timer error resets the current match
to waiting, clears scores/readiness and notifies connected players. Cleanup only
applies while that task still owns the current room; it cannot clear a replacement
timer. Transport-detected disconnects during preparation/play use the same reset
path. Finished results remain immutable.
Failed/cancelled handshakes remove only their own empty room. An accept that
completes after its room was deleted is rejected instead of joining the old room.

## Deployment and validation limits

Deploy this frontend/backend contract together. Old clients interpret
`GAME_START` as immediate play and are not compatible with this lifecycle.
Rooms and timer tasks remain in a single Python process: use one backend worker
for PK rooms. Restart recovery, shared multi-worker storage, anti-cheat and
resuming an interrupted match are outside this change. Scores remain
client-reported, with server validation of sequence and time only.
Network loss is recognized when the WebSocket transport detects it; this does
not add a reconnect grace period or persistent result history.
Result delivery has no acknowledgement/replay guarantee if a connection fails.
The unchanged score protocol has no match identifier: a delayed frame from a
surviving socket that happens to be the next valid score in a later match cannot
be distinguished from a fresh frame. Clients must stop analysis and discard old
score sends on interruption; adding a match identifier would require a separate
frontend/backend protocol change. Room identity/task checks protect server tasks
and replaced sockets, while scores remain client-reported.

Automated tests use controlled clocks/transport, FastAPI TestClient and mocked
browser boundaries. They do not establish real camera accuracy, full
Better Auth/FastAPI integration or deployed network latency behavior.
`tests/test_pk_timer_lifecycle.py` controls only the monotonic/wall clocks and
async sleep/transport boundaries; it runs the production wait loop, timer,
broadcast, score validation and cleanup methods. Queue futures and events control
ordering without waiting 10 or 60 seconds. The regression matrix covers concurrent
and repeated readiness, strict score order, both deadline boundaries, natural
expiration and all results, interrupted/recreated rooms, blocked operations and
notifications, cancellation/errors, and each lifecycle broadcast failing for
either or both recipients. WebSocket tests also reject client-sent lifecycle
events and malformed messages through the actual route.

## Manual verification

1. Run matching frontend/backend versions with local test auth and one backend
   worker. Sign in as two distinct users in separate browser profiles.
2. Join the same four-digit room and exercise. Confirm third-player,
   duplicate-user, invalid Origin and expired-token rejection.
3. Prepare one player, then both. Verify preparation lasts 10 seconds, no reps
   count during preparation, and both switch to the 60-second match.
4. Perform full exercises with visible landmarks. Confirm opponent counts,
   PAUSED/LOST behavior and existing pose hints. At expiration, verify no further
   score changes and matching final win/lose/draw results on both screens.
5. Repeat with one player leaving during preparation and during play. Verify
   survivor returns to waiting, scores/readiness clear, rejoin requires both
   ready, and no old timer ends the new match.
6. Test normal and abnormal socket closure, delayed/duplicate events, background
   tabs and throttled networking. Verify display recovery, final server results,
   camera cleanup on leave and both desktop/mobile layouts.
