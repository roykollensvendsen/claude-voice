# Ask the voice what it needs

The main user of the bridge's tools is not a person. It is the Claude that
answers the owner in the Claude app's voice mode. It chooses which tools to
load, calls them, waits on them, and reads their answers aloud while the owner
drives. Whether a tool is good is decided there, so the voice is asked.

This page says how to ask it, what to ask, and what to do with the answer. It
applies to the bridge and to every plugin.

## When

- After a new tool or plugin lands and is running behind the bridge.
- After a tool's name, first line, inputs or answer changes.
- When the owner says the voice was slow, silent, confused or noisy.

## What the voice sees

Before it loads a tool, the voice sees only a list of names, each with the
start of its description. A description is cut after a short stretch, so the
first sentence is all it has to choose by. Only once it loads a tool does it
see the rest and the inputs. Each call must answer within the app's time limit,
and every restart of the bridge drops the connection mid-call.

So most problems show up as one of these: the voice did not find a tool, picked
the wrong one, loaded several to find the right one, polled where it should
have waited, or made noise on the call.

## How to ask

Start a voice call, use the new tool once for real, and then ask the voice
these, in its own words:

1. Which tools did you see before loading any, and was it clear from the first
   line which one to use?
2. Which tools did you confuse, or have to load several of before finding the
   right one?
3. Where did you have to call something again, poll, or restart a wait?
4. Where was there silence, or noise, on the call because of a tool?
5. What tool, input or answer would have made this easier?

Ask it to answer briefly, and pass the answer on as it is.
[`side_question`](../README.md) is a good way to check progress in a session
while the call goes on, without disturbing that session.

## Turning the answer into work

- Each point becomes one issue, or one pull request when the fix is clear,
  written in the voice's words with what it tried and what happened.
- A point about a first line goes to the rule that a first line is seen whole,
  in the [plugin standard](plugin-standard.md).
- A point about another plugin goes to that plugin's session, through the
  manager ([organisation](organisation.md)).
- When a point touches a contributor's tool, mention them in the issue.

## The read-back check

After the change is running, the voice is asked once more. It reads aloud the
first line of the changed tool as it sees it before loading, and says whether
it would now choose it. If it would not, the change is not finished.
