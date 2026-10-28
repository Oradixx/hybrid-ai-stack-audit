# Auditing a hybrid AI coding stack on a 24 GB Mac

What I measured, what I fixed, and what turned out to be unused. A case study from October 2026.

> One machine, one person, small samples. Every number says how it was obtained:
> - **measured**: on my machine;
> - **reported**: from a third-party source;
> - **estimated**: calculated.

## TL;DR

- **Measure usage before optimizing.** I spent two weeks tuning a local-model path: llama.cpp, Qwen3.6-35B-A3B, an agent CLI, and an
  MCP bridge. Ten days after the audit, the usage logs showed:
  - the bridge had been loaded in 55 Claude Code sessions and called **zero** times;
  - the agent CLI had been used interactively twice, ever, both times on the same day.
- **Low Power Mode halves local inference speed** on Apple Silicon: 887 → 409 tok/s prompt processing, 46.8 → 20.4 tok/s generation
  (measured with `llama-bench`).
- **One runaway test wrote 2.64 TB to the SSD**, through 4 hours of swap while the agent waited on it with no timeout. That is about
  70 % of everything the drive had ever written.
- **Background swap on a 24 GB Mac carrying a 13 GB model is normal** (≈ 1.3 GB per hour), and it had no measurable effect on
  throughput.
- **Claude Code can load your instructions twice.** A symlinked `~/.claude/AGENTS.md` is read as *project* instructions in every
  session started under `$HOME`, on top of an `@import` from `~/.claude/CLAUDE.md`.
- **Search by function, not by name.** Two "nothing like this exists" verdicts from my own audit were wrong.

## The stack

- **Orchestrator**: Claude Code (Opus) on a Claude Pro subscription. It delegates to Claude subagents (Haiku, Sonnet).
- **Free cloud models**: OpenCode Zen, restricted to zero-retention models for private code.
- **Local model**: Qwen3.6-35B-A3B, UD-IQ3_XXS GGUF, ≈ 13 GB resident.
  - Engine: llama.cpp `llama-server` in router mode, 2 slots, 49,152-token shared context, q8_0 KV cache.
  - Machine: MacBook with an Apple M5 and 24 GB of memory.
- **Around the local model**: an agent CLI (Crush), and a home-made MCP server that exposed the local model to Claude Code as tools.

## Method

- **Phases.** Baseline measurements, then production fixes, a blind model comparison, a build-or-adopt review, and this write-up.
  Each phase was gated by my approval.
- **Records.** Decisions are written as ADRs. Every claim is labelled measured, reported, estimated or unverified.
- **Starting point.** The initial brief, written with another LLM, was treated as hypotheses only.

The model comparison used a bench graded by execution:
- **Isolation**: a real agent CLI under macOS `sandbox-exec`, network limited to localhost.
- **Grading**: tests validated against mutants, plus a held-out set frozen by a SHA-256 manifest.
- **Ordering**: task order randomised per pass, with a fixed seed.
- **Guards**:
  - campaigns refuse to start on battery or in Low Power Mode;
  - the thermal state is logged;
  - one time limit and one memory limit apply to the whole process tree.

## Findings

### 1. Low Power Mode halves throughput

| Power | pp512 | tg128 |
|---|---|---|
| AC | 887 tok/s | 46.8 tok/s |
| Battery, Low Power Mode | 409 tok/s | 20.4 tok/s |

Measured with `llama-bench`, same model and build. On battery, `pmset -g custom` showed `lowpowermode 1`. I found no published
measurement of this (short search, not exhaustive).

Rule I now follow: benchmark on AC only, and record the power source with every measurement. My campaign script refuses to start
otherwise.

### 2. An agent waiting forever on a leaking test

**What happened.**
- In an overnight campaign, one task's test leaked memory.
- The agent called its job-output tool with `wait: true`. That tool has no timeout in the CLI version I used (Crush v0.98.1, from
  reading the code).
- It waited 4 hours. macOS swapped out 161,039,504 pages of 16 KiB: **2.64 TB** written to the SSD. That is ≈ 70 % of the 3.75 TB
  the drive had written in its whole life (SMART data).
- An upstream fix is open: [charmbracelet/crush#3411](https://github.com/charmbracelet/crush/pull/3411).

**What worked: a memory watchdog** ([`scripts/agent-memory-watchdog.sh`](scripts/agent-memory-watchdog.sh)).
- It kills any descendant of the agent above 1 GB resident, checking every 2 s.
- Tested with the launchd agent installed: a leaking child was killed at 2.35 GB, and the parent kept running.

**What did not work: a hook rewriting `wait: true` into `wait: false`.**
- Fair test: a realistic task, with no hint about how to wait.
- The hook failed 1 run in 3, and made the model poll frantically: 20 or more calls, against 2 without the hook.
- Rejected.

### 3. Background swap is normal here, and harmless

- **How often.** 24 of 288 benchmark runs added swap. Excluding the runaway, that is ≈ 1.3 GB per hour of activity.
- **How much per affected run.** Median 308 MB, p90 744 MB, max 1.2 GB.
- **Why.** About 400 MB stays free once the model is loaded. Swap appeared at the same rate whatever the task: it comes from the
  memory margin, not from the workload.
- **Effect on speed.** None measured: median generation speed stayed at 27–29 tok/s per pass all night.
- **New threshold.** I replaced "zero swap" with a bounded threshold: ≤ 1 GB per run and ≤ 2 GB per hour. It is valid only while the
  watchdog runs.

### 4. Context is cheap on a hybrid MoE

- **Measured.** Server memory went from 13.07 GB to 13.44 GB between 23k and 63k tokens of context.
- **Estimated from those two points.** ≈ 0.0094 GB per 1k tokens.
- **The gap.** A generic sizing script I used assumed 0.1875 GB per 1k tokens, 20 times too much. It ignored that only the attention
  layers of this hybrid model (gated DeltaNet plus attention) keep a KV cache, and it ignored the unified KV cache.
- **In production.** The model runs at 49,152 tokens.

### 5. The model comparison was inconclusive, and that is the result

- **Size.** 4 arms (2 models × 2 sampling settings) × 24 dev tasks × 3 passes = 288 runs.
- **Results.** Mean pass rates ranged from 90 to 97 %. The best arm beat the incumbent on every pass, by +5.6 points on average. That
  stays below the threshold fixed before the campaign: +10 points and non-overlapping min–max ranges.
- **Why it cannot conclude.** The dev set is at ceiling. With 24 tasks and 3 passes, it cannot detect a gap under ~10 points.
- **Decision.** Keep the existing model, and write harder tasks before any new comparison. The held-out set stays untouched.

### 6. Privacy: a rule is not a control

**The leak.** A delegation rule in my agent instructions was sending private code to a free model whose provider may train on it.

**The fix was configuration, not a better sentence.**
- Every model outside an allow-list is disabled in `opencode.json`
  ([`scripts/opencode-whitelist.py`](scripts/opencode-whitelist.py)), so no orchestrator can pick one.
- Later, one orchestrator's model turned out not to read my instruction file at all (tested). The allow-list still bounded where it
  could delegate, but not what it could read: an agent with a shell can open any file in your home directory and send it to its
  provider. That is true of every coding agent with shell access.

**A local model does not make a delegated sub-task private.** When a cloud orchestrator delegates a "confidential" sub-task to a
local model, the orchestrator has already written the prompt, so the content went through the cloud. To keep something on the
machine, start the local agent directly.

**Routers I excluded.** Some popular routers and plugins reuse the Claude Code OAuth token:
- one reads it from the macOS Keychain and replays it as a bearer token
  ([claude-code-router](https://github.com/musistudio/claude-code-router/blob/057abaa1f95519ab8faf999e1ccef2b80ccdeb9e/packages/core/src/agents/local-providers/claude-code.ts#L399));
- another forwards a subscription token received in `Authorization`
  ([LiteLLM](https://github.com/BerriAI/litellm/blob/5012083b721020ba6115a4519f40d3746eee6bc4/litellm/llms/anthropic/common_utils.py#L324-L360)).

Anthropic's [legal and compliance page](https://code.claude.com/docs/en/legal-and-compliance) forbids third parties from relaying
Claude.ai credentials, so I excluded them.

## What my own audit got wrong

### 1. No usage metric

All seven indicators measured performance. Here is what the logs showed ten days later (aggregate counts only, no session content):

| Component | Usage, Oct 18–28 |
|---|---|
| MCP bridge to the local model | loaded in 55 Claude Code sessions, 0 tool calls |
| Agent CLI for the local model | 2 interactive sessions, ever, both on the same day |
| Memory watchdog | 0 real interventions |
| Local model | not loaded at the end of the audit |

**Consequences.**
- I removed the bridge from Claude Code, dropped the agent CLI and stopped the watchdog.
- I kept the local model for the rare case where nothing may leave the machine.

**Plausible cause of the zero calls (unverified).** Claude Code defers MCP tools: only their names are visible until the model
searches for them. A server's `instructions` field, by contrast, is shown. A model does not delegate to a tool it can barely see.

### 2. "Unique" verdicts checked by name

I had concluded that my bench and my bridge had no equivalent. Both verdicts were wrong.
- [LocalAgentBenchmark](https://github.com/gloutchov/LocalAgentBenchmark) and
  [localagentbench](https://github.com/seraphonixstudios/localagentbench) already benchmark local coding agents, graded by execution.
  The first even uses a macOS Seatbelt profile stricter than mine.
- At least eight MCP servers delegate from Claude Code to a local model. [houtini-lm](https://github.com/houtini-ai/houtini-lm) is the
  cleanest I read.

What remained unique, the Mac power, thermal and swap guards, is small.

### 3. Doubled instructions

**Reproduce it:**

```bash
mkdir -p ~/.cache/t && cd ~/.cache/t && claude -p --model haiku "List the absolute paths of the instruction files in your context."
```

**What I saw.** `~/.claude/AGENTS.md` was a symlink to the same file that `~/.claude/CLAUDE.md` imports:
- a session started anywhere under `$HOME` listed the file twice, once as user instructions and once as project instructions;
- a session started from `/private/tmp` listed it once.

Claude Code 2.1.304, measured. Removing the symlink fixed it. Trimming the file also helped: 7 KB down to 5 KB, now loaded once.

### 4. Judge and party

I built the bench and the bridge, then rated them "unique, keep". Usage numbers and a review with fresh context corrected that. Get
a third-party pass on your own verdicts.

### 5. Experimenter bias

My first test of the hook told the model exactly how to wait, so it said nothing about real behaviour. The fair retest rejected the
hook.

## Scripts

- [`scripts/agent-memory-watchdog.sh`](scripts/agent-memory-watchdog.sh) kills descendants of an agent process above a memory
  threshold. macOS has no cgroups. Bash, no dependencies; a launchd example is in the header.
- [`scripts/opencode-whitelist.py`](scripts/opencode-whitelist.py) disables, in `opencode.json`, every model of the `opencode`
  provider except an allow-list. With `--check`, it exits non-zero if a model outside the list is still visible.
- [`scripts/usage-report.py`](scripts/usage-report.py) counts sessions per client and calls to an MCP bridge, from local logs
  (aggregates only). The paths are those of my setup: adapt them.

## Limits

- **One machine**: Apple M5, 24 GB, macOS 27.
- **One user.**
- **Versions of October 2026**: llama.cpp build 11429, Crush v0.98.1, OpenCode v2.0.29, Claude Code 2.1.x.
- **Small samples**: 288 benchmark runs, 10 days of usage.
- **Third-party statements**, such as zero retention, are reported, not verified at the provider.

## License

MIT, see [LICENSE](LICENSE).
