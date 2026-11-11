# My rules for the voice

<!--
An example for voice_instructions_file in ~/.config/claude-voice/config.toml.
Copy it outside the repository, replace each <placeholder>, and remove what you
do not want. The bridge adds this text to the voice's instructions under
"The owner's own rules:", and uses only the first 8 KB. Restart the bridge after
a change. Keep personal details such as names, devices and addresses out of
anything you share.
-->

## Where work goes

- A proposal, a new idea or a change to how things are done goes to the session
  called `<manager session name>`, with `message_active_session`. Do not start a
  new session for it.
- A small question about facts that only needs reading, such as "is the build
  green?", goes to the session in the `<helper role>` role, with
  `ask_active_session`. Start it with `start_role` if it is not running. It
  only reads; it never changes anything.
- Anything else goes to the session I name. If I name none, ask me which one.

## How to talk to me

- Ask one question at a time, and wait for my answer before the next.
- Keep answers short; I am usually listening, not reading.
- Say numbers and names plainly; do not read out file paths or long ids.

## What waits on me

- Gather everything that needs me, such as approvals, decisions and commands I
  must type, and say it at the end, after the rest.
- Say each one once. Do not repeat a list I have already heard unless it changed.
