---
name: skill-creation
description: Create new skills, improve existing skills, and test them. Use when the user asks to create a skill, save a workflow as a skill, turn a conversation into a skill, add a new skill, or make a reusable skill from what we just did.
---

# Skill Creation

A skill for creating and iteratively improving GUS skills.

The core loop:

1. Understand what the skill should do
2. Write a draft
3. Run test prompts using the skill
4. Review results with the user
5. Improve and repeat until satisfied

Figure out where the user is in this process and jump in. If they say "create a skill from this conversation", extract the workflow from history. If they already have a draft, go straight to testing.

---

## Step 1 — Capture Intent

If the current conversation contains a workflow the user wants to capture, extract answers from the history first: tools used, sequence of steps, corrections made, input/output formats. The user may need to fill gaps and should confirm before you proceed.

Otherwise, ask:

1. What should this skill enable GUS to do?
2. When should it trigger? (what user phrases, contexts)
3. What does the output look like?

Don't write anything until you understand the answers.

---

## Step 2 — Interview

Ask about edge cases, input/output formats, success criteria, and dependencies. Keep it short — one round of questions is usually enough. Come prepared so you reduce the burden on the user.

---

## Step 3 — Write the SKILL.md

Create the folder and file:

```bash
mkdir -p .gus/skills/<name>
```

Write `.gus/skills/<name>/SKILL.md`.

### Spec requirements (enforced at load time)

| Field | Required | Constraints |
|---|---|---|
| `name` | Yes | 1–64 chars, `a-z 0-9 -` only, no `--`, no leading/trailing `-`, must match folder name |
| `description` | Yes | 1–1024 chars, non-empty |
| `license` | No | License name or path to bundled file |
| `compatibility` | No | 1–500 chars, environment requirements only |
| `metadata` | No | Nested YAML key-value map |
| `allowed-tools` | No | Space-separated tool names (experimental) |

### Writing the description

The description is the primary trigger mechanism — it determines whether GUS activates the skill. Two rules:

1. Say both **what the skill does** and **when to use it** (specific trigger keywords)
2. Make it slightly "pushy" — lean forward rather than being conservative. Undertriggering is a bigger problem than overtriggering. Instead of *"Helps with deployment"*, write *"Handles deployment to staging. Use when the user mentions deploying, releasing, or pushing to staging, even if they don't say 'deploy' explicitly."*

### Writing the body

- Use numbered steps, imperative form
- Explain **why** behind important instructions — don't just write MUST in caps
- Keep under 500 lines; move long reference material to `references/`
- Reference scripts or docs with relative paths (e.g. `scripts/run.sh`)

### Optional subdirectories

```
.gus/skills/<name>/
├── SKILL.md
├── scripts/      — executable helpers
├── references/   — docs loaded on demand
└── assets/       — templates, data files
```

### Minimal valid example

```
---
name: roll-dice
description: Roll dice and return a random result. Use when the user asks to roll a die, roll d6, roll d20, or wants a random number from a dice roll.
---

Run:

    echo $((RANDOM % <sides> + 1))

Replace <sides> with the number of sides (6, 20, etc.).
```

---

## Step 4 — Test the skill

After writing, come up with 2–3 realistic test prompts — things a real user would type. Share them with the user: *"Here are some test cases I'd like to try. Do these look right?"*

Then test each one by running GUS's own tool-use against the prompt with the skill available. Observe:
- Did GUS load the skill? (look for `🧠 GUS load skill` in the output)
- Did it follow the instructions correctly?
- Was the output what the user expected?

---

## Step 5 — Iterate

Based on what you observe and the user's feedback:

1. **Generalize** — don't fix just the test case; fix the underlying pattern
2. **Keep it lean** — remove instructions that aren't pulling their weight
3. **Explain the why** — reframe rigid rules as reasoning the model can apply flexibly
4. **Improve the description** if the skill didn't trigger when it should have

Rewrite the skill, run the test prompts again, repeat until the user is satisfied.

---

## Confirm

When done, tell the user:
- The full path of the created SKILL.md
- The skill is already active (GUS detects it automatically after creation)
- It triggers via `/<name>` or automatically when a matching task is described

---

## Installing skills from anthropics/skills

Skills from `https://github.com/anthropics/skills` follow the same agentskills.io format. Copy the folder into `.gus/skills/`:

```bash
cp -r path/to/skills/<skill-name> .gus/skills/
```
