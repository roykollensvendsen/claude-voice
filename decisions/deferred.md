# What is deliberately not done yet

A thing left undone with no trigger is a thing forgotten. Each row names what
would make it worth doing, so the question comes back on its own rather than
depending on someone remembering it. Nothing enforces a trigger; the list is
read whenever a decision record is written and when a phase ends.

| Not done | The trigger | Why not now |
|---|---|---|
| A coverage threshold in CI | Two months of measured coverage to set it below | A number picked before there is data is a guess with a gate on it |
| A code of conduct and issue templates | A second contributor, or the first outside issue | Boilerplate answering questions nobody has asked |
| A release workflow and publishing | The first release someone outside will install | A published name is claimed and a published version cannot be reused |
| `CODEOWNERS` | A second person who reviews | It would name one person as the owner of everything |
| `pre-commit` as a requirement rather than an option | A second contributor | The gates run in CI, and a hook one person installs is a hook one person maintains |
| Ending a terminal session from the bridge | The owner needs to stop a session while away from the computer | Killing a process someone may be working in cannot be undone; it would need its own tool, a spoken yes, and an idle session |
| Pushing news to the phone instead of being asked | A voice client that accepts notifications from an MCP server | Today's clients only pull, so `whats_new` is polled |
| ChatGPT as the voice front | OpenAI lets a Plus plan on Android use a custom MCP server | It could not connect at all when this was built |
| Ranked or semantic search of transcripts | Keyword search misses something the owner asked for | Keyword search answered every case tried so far |
| A shorter `list_projects` for a large root | The owner finds the folder list too long to hear | With the home folder as root it lists hundreds of folders, and asking for recent sessions works better |
