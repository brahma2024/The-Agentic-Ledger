# Agent Workflow Architecture

A reference guide for building agentic workflows with open-source LLMs. This document captures the mental model, project structure, and step-by-step build sequence so the approach is reusable across any project.

---

## 1. Core Mental Model

Every agent framework — CrewAI, AutoGen, LangGraph, OpenAI Assistants — is a variation of the same loop:

```
┌─────────────────────────────────────────────────────────┐
│                                                         │
│   Skill.md ──► LLM ──► Tool Call ──► Tool Result ──┐   │
│   (system       (decides    (execute)    (observe)  │   │
│    prompt)       action)                            │   │
│                                                     │   │
│                  ◄──────────────────────────────────┘   │
│                  (loop until done)                       │
│                                                         │
│                  ──► Structured Output                   │
│                       (final report)                     │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**The three primitives:**

| Primitive | What It Is | Example |
|-----------|-----------|---------|
| **Skill** | A system prompt that defines identity, methodology, and output format | `security-scan.md` — "You are a security engineer. Check for hardcoded secrets by grepping for `sk-\|AKIA`..." |
| **Tools** | Functions the LLM can call via structured tool_calls | `read_file(path)`, `grep(pattern, path)`, `run_command(cmd)` |
| **Loop** | The orchestrator that sends messages to the LLM, executes tool calls, and appends results | A ~50-line while loop that runs until the LLM stops requesting tools |

The insight: **frameworks don't add magic — they add convenience wrappers around this loop.** Understanding the raw loop means you can debug any framework, swap any component, and build exactly what you need.

### Why This Matters

When you see a CrewAI "Agent" with "tools" and a "task", you're looking at:
- Agent = system prompt (skill.md)
- Tools = function definitions passed to the LLM
- Task = user message
- Crew = orchestrator loop that manages multiple agents

When you see a LangGraph "node", you're looking at:
- Node = a function that calls an LLM with a system prompt and tools
- Edge = routing logic between nodes
- State = the accumulated messages/results passed between nodes

Strip away the abstraction and the core is always: **prompt → LLM → tool_call → execute → loop**.

---

## 2. Stack Selection Guide

### Recommended Open-Source Stack

| Component | Recommendation | Why |
|-----------|---------------|-----|
| **Thinking LLM** | DeepSeek-R1 (distilled 14B/32B) | Chain-of-thought reasoning, strong planning, fits consumer GPUs |
| **Coding LLM** | Qwen-2.5-Coder-32B-Instruct | Best open-source code model, strong tool-calling support |
| **Serving (learning)** | Ollama | One-line install, model management, good enough for development |
| **Serving (production)** | vLLM | Continuous batching, PagedAttention, 3-5x throughput over Ollama |
| **Framework (learning)** | Raw Python | Understand the loop before adding abstractions |
| **Framework (production)** | LangGraph | Stateful graphs, checkpointing, human-in-the-loop, streaming |

### Model Selection Decision Tree

```
Is the task primarily about planning/reasoning?
  YES → DeepSeek-R1 (14B for fast iteration, 32B for quality)
  NO  → Is the task about code reading/generation?
          YES → Qwen-2.5-Coder-32B-Instruct
          NO  → Is the task about general text?
                  YES → Llama-3.1-8B (fast) or 70B (quality)
                  NO  → Evaluate domain-specific models
```

### Hardware Requirements

| Model | VRAM (FP16) | VRAM (Q4) | Notes |
|-------|------------|-----------|-------|
| DeepSeek-R1-14B | 28 GB | 8 GB | Fits RTX 4090 quantized |
| Qwen-2.5-Coder-32B | 64 GB | 20 GB | Needs A100 or 2x RTX 4090 for FP16 |
| Llama-3.1-8B | 16 GB | 5 GB | Fits any modern GPU |

### Cloud API Fallback

When local hardware isn't sufficient:

| Provider | Model | Input (per 1M) | Output (per 1M) |
|----------|-------|----------------|-----------------|
| OpenAI | GPT-4o | $2.50 | $10.00 |
| OpenAI | GPT-4o-mini | $0.15 | $0.60 |
| Anthropic | Claude Sonnet 4.5 | $3.00 | $15.00 |
| DeepSeek | DeepSeek-R1 (API) | $0.55 | $2.19 |
| Together AI | Qwen-2.5-Coder-32B | ~$0.80 | ~$0.80 |

---

## 3. Tool Layer Design

Every code-analysis agent needs 5 core tools. These map directly to what Claude Code, Cursor, and Aider use internally.

### Tool 1: `file_reader.py`

```python
def read_file(path: str, offset: int = 0, limit: int = 2000) -> str:
    """Read file contents with optional line range."""
    with open(path, 'r') as f:
        lines = f.readlines()
    selected = lines[offset:offset + limit]
    return ''.join(f"{i+offset+1:4d} | {line}" for i, line in enumerate(selected))
```

**Tool Schema (OpenAI function-calling format):**
```json
{
  "type": "function",
  "function": {
    "name": "read_file",
    "description": "Read the contents of a file with line numbers. Use offset/limit for large files.",
    "parameters": {
      "type": "object",
      "properties": {
        "path": {"type": "string", "description": "Absolute or relative file path"},
        "offset": {"type": "integer", "description": "Starting line number (0-indexed)", "default": 0},
        "limit": {"type": "integer", "description": "Max lines to return", "default": 2000}
      },
      "required": ["path"]
    }
  }
}
```

### Tool 2: `file_search.py`

```python
import glob as glob_mod

def glob_search(pattern: str, root: str = ".") -> list[str]:
    """Find files matching a glob pattern."""
    matches = sorted(glob_mod.glob(pattern, root_dir=root, recursive=True))
    return matches[:500]  # Cap results
```

**Tool Schema:**
```json
{
  "type": "function",
  "function": {
    "name": "glob_search",
    "description": "Find files matching a glob pattern (e.g., '**/*.py', 'src/**/*.ts').",
    "parameters": {
      "type": "object",
      "properties": {
        "pattern": {"type": "string", "description": "Glob pattern to match"},
        "root": {"type": "string", "description": "Root directory to search from", "default": "."}
      },
      "required": ["pattern"]
    }
  }
}
```

### Tool 3: `content_search.py`

```python
import subprocess

def grep(pattern: str, path: str = ".", file_type: str = None) -> str:
    """Search file contents using ripgrep."""
    cmd = ["rg", "--no-heading", "-n", pattern, path]
    if file_type:
        cmd.extend(["--type", file_type])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    lines = result.stdout.strip().split('\n')
    return '\n'.join(lines[:200])  # Cap output
```

**Tool Schema:**
```json
{
  "type": "function",
  "function": {
    "name": "grep",
    "description": "Search for a regex pattern in file contents. Returns matching lines with file paths and line numbers.",
    "parameters": {
      "type": "object",
      "properties": {
        "pattern": {"type": "string", "description": "Regex pattern to search for"},
        "path": {"type": "string", "description": "File or directory to search", "default": "."},
        "file_type": {"type": "string", "description": "File type filter (e.g., 'py', 'js', 'ts')"}
      },
      "required": ["pattern"]
    }
  }
}
```

### Tool 4: `command_runner.py`

```python
import subprocess
import shlex

ALLOWED_COMMANDS = {
    "git", "ls", "wc", "du", "file", "head", "tail",
    "docker", "pip", "npm", "cat", "find"
}

def run_command(cmd: str) -> str:
    """Execute a shell command from an allowlist (read-only operations only)."""
    parts = shlex.split(cmd)
    if parts[0] not in ALLOWED_COMMANDS:
        return f"ERROR: Command '{parts[0]}' not in allowlist: {ALLOWED_COMMANDS}"
    # Block destructive flags
    dangerous = {"rm", "-rf", "--force", "push", "delete", "drop"}
    if any(flag in parts for flag in dangerous):
        return "ERROR: Destructive operation blocked"
    result = subprocess.run(parts, capture_output=True, text=True, timeout=30)
    return result.stdout[:10000] or result.stderr[:2000]
```

**Tool Schema:**
```json
{
  "type": "function",
  "function": {
    "name": "run_command",
    "description": "Execute a sandboxed shell command. Only allowlisted read-only commands permitted.",
    "parameters": {
      "type": "object",
      "properties": {
        "cmd": {"type": "string", "description": "Shell command to execute"}
      },
      "required": ["cmd"]
    }
  }
}
```

### Tool 5: `git_inspector.py`

```python
import subprocess

def git_log(max_count: int = 20) -> str:
    """Get recent git commit history."""
    result = subprocess.run(
        ["git", "log", f"--max-count={max_count}", "--oneline", "--decorate"],
        capture_output=True, text=True
    )
    return result.stdout

def git_diff(ref: str = "HEAD~1") -> str:
    """Get git diff against a reference."""
    result = subprocess.run(
        ["git", "diff", ref, "--stat"],
        capture_output=True, text=True
    )
    return result.stdout[:10000]

def git_ls_files() -> str:
    """List all tracked files."""
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True, text=True
    )
    return result.stdout
```

**Tool Schema:**
```json
{
  "type": "function",
  "function": {
    "name": "git_inspector",
    "description": "Inspect git repository state: log, diff, or list tracked files.",
    "parameters": {
      "type": "object",
      "properties": {
        "action": {
          "type": "string",
          "enum": ["log", "diff", "ls_files"],
          "description": "Git operation to perform"
        },
        "ref": {"type": "string", "description": "Git ref for diff (default: HEAD~1)"},
        "max_count": {"type": "integer", "description": "Max commits for log (default: 20)"}
      },
      "required": ["action"]
    }
  }
}
```

---

## 4. Skill.md Anatomy

A skill prompt has three sections that determine agent quality:

### Structure

```markdown
# [Agent Name]

## Identity & Scope
You are a [specific role]. Your task is to [specific objective].
You will [what you do]. You will NOT [boundaries].

## Methodology
Follow this exact sequence:

### Phase 1 — [Name]
1. [Specific action with exact tool call]
2. [Specific pattern to search for]
3. [Decision logic based on results]

### Phase 2 — [Name]
...

## Output Format
Produce a report in exactly this format:
[Exact markdown template]
```

### Why Methodology Specificity Matters

Compare these two approaches:

**Vague (poor results):**
> "Check the codebase for security issues and report your findings."

**Specific (precise results):**
> "Search for hardcoded secrets using these patterns:
> - `grep('sk-proj|sk-|AKIA|ghp_|password\s*=|secret\s*=|token\s*=|Bearer |-----BEGIN')`
> - Check every `.env` file: `glob_search('**/.env*')`
> - Check git history for accidentally committed secrets: `run_command('git log --all --diff-filter=A -- *.env *.pem *.key')`
>
> For each finding, record: file path, line number, the pattern matched, severity (CRITICAL if it's a live key, HIGH if it's a pattern that could contain keys)."

The specific version:
- Tells the agent **which tools to use** and **what arguments to pass**
- Defines **classification criteria** so the agent doesn't have to guess
- Produces **consistent results** across different runs and models
- Works even with smaller models that can't infer methodology from vague instructions

### Writing Effective Methodology Sections

1. **Be tool-aware**: Reference the exact tool functions available
2. **Include specific patterns**: Regex patterns, file paths, command flags
3. **Define decision points**: "If X, then classify as CRITICAL. If Y, classify as HIGH."
4. **Order matters**: Discovery phases first, analysis second, reporting last
5. **Cap scope**: Set explicit boundaries ("Focus on application code, not vendored dependencies")

---

## 5. Agent Loop Implementation

The core loop is ~50 lines of Python. Everything else is configuration.

### Pseudocode

```python
import json
from pathlib import Path

def run_agent(
    skill_path: str,
    user_prompt: str,
    model: str = "qwen2.5-coder:32b",
    max_turns: int = 30,
    llm_client = None,       # Ollama/vLLM/API client
    tool_executor = None      # Maps tool_call name → function
):
    # 1. Load skill as system prompt
    system_prompt = Path(skill_path).read_text()

    # 2. Load tool schemas
    tool_schemas = tool_executor.get_schemas()  # List of OpenAI-format tool defs

    # 3. Initialize conversation
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    # 4. Agent loop
    for turn in range(max_turns):
        # Call LLM with tools
        response = llm_client.chat(
            model=model,
            messages=messages,
            tools=tool_schemas,
            temperature=0.1
        )

        assistant_message = response.message
        messages.append(assistant_message)

        # Check if LLM wants to call tools
        if not assistant_message.tool_calls:
            # LLM is done — return final response
            break

        # Execute each tool call
        for tool_call in assistant_message.tool_calls:
            result = tool_executor.execute(
                name=tool_call.function.name,
                arguments=json.loads(tool_call.function.arguments)
            )
            # Truncate large results to preserve context window
            if len(result) > 8000:
                result = result[:8000] + "\n... [truncated]"

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result
            })

    # 5. Extract and return final response
    final_response = messages[-1].get("content", "")
    return final_response
```

### Key Implementation Details

**Temperature**: Use `0.1` for deterministic tool-calling behavior. Higher temperatures cause inconsistent tool argument formatting.

**Max turns**: 30 is a good default. Simple tasks complete in 5-10 turns. Complex scans use 15-25. If hitting 30, the skill prompt likely needs refinement.

**Tool call ID**: Required by the API format. Each tool result must reference the `tool_call.id` it responds to.

**Result truncation**: Critical for context window management. A single `git log` or `grep` can return megabytes. Truncate to 8K characters per tool result.

---

## 6. Production Design Patterns

### Pattern 1: Context Window Management

The #1 production issue. A 32K context model fills up after ~15 tool calls with large results.

**Strategies:**
- **Truncate tool results**: Cap at 8K characters per result (shown above)
- **Summarize between phases**: After Phase 1 (Discovery), extract key findings into a summary, start Phase 2 with a fresh context + summary
- **Use targeted searches**: `grep` with specific patterns instead of reading entire files
- **Progressive disclosure**: Read file listings first, then read specific files

### Pattern 2: Multi-Phase Execution

Break large scans into phases with context handoff:

```python
# Phase 1: Discovery
discovery_report = run_agent(
    skill_path="skills/security-scan-discovery.md",
    user_prompt="Scan the codebase at /path/to/project"
)

# Phase 2: Deep Scan (uses discovery findings as context)
deep_scan = run_agent(
    skill_path="skills/security-scan-analysis.md",
    user_prompt=f"Analyze these findings:\n{discovery_report}"
)

# Phase 3: Report Generation
final_report = run_agent(
    skill_path="skills/security-scan-report.md",
    user_prompt=f"Generate final report from:\n{deep_scan}"
)
```

### Pattern 3: Thinking + Coding Model Split

Use the right model for each phase:

| Phase | Model | Why |
|-------|-------|-----|
| Planning | DeepSeek-R1-32B | Chain-of-thought reasoning for strategy |
| Code reading | Qwen-2.5-Coder-32B | Best code comprehension |
| Report writing | DeepSeek-R1-14B | Structured reasoning for classification |

```python
# Planner decides what to investigate
plan = run_agent(skill="planner.md", model="deepseek-r1:32b", ...)

# Coder reads and analyzes the code
findings = run_agent(skill="analyzer.md", model="qwen2.5-coder:32b",
                     user_prompt=f"Investigate: {plan}")

# Reporter synthesizes findings
report = run_agent(skill="reporter.md", model="deepseek-r1:14b",
                   user_prompt=f"Report on: {findings}")
```

### Pattern 4: Tool Call Sandboxing

Never let an LLM execute arbitrary commands. Defense in depth:

1. **Allowlist commands** (shown in `command_runner.py`)
2. **Block dangerous flags** (`--force`, `-rf`, `push`, `delete`)
3. **Timeout all operations** (30 seconds max)
4. **Run in container** for production deployments
5. **Log all tool calls** for audit trail

### Pattern 5: Multi-Agent Composition

For complex workflows, compose specialized agents:

```
Orchestrator Agent
├── Discovery Agent      → understands the codebase
├── Security Agent       → finds vulnerabilities
├── Cost Analysis Agent  → calculates costs
└── Report Writer Agent  → synthesizes everything
```

The orchestrator:
1. Runs Discovery Agent, gets codebase summary
2. Passes summary to Security + Cost agents (can run in parallel)
3. Collects all findings, passes to Report Writer
4. Returns final report

```python
# Orchestrator pseudocode
discovery = run_agent("skills/discovery.md", project_path)

# Run specialists in parallel (asyncio/threading)
security_findings = run_agent("skills/security.md", discovery)
cost_findings = run_agent("skills/finops.md", discovery)

# Synthesize
report = run_agent("skills/report-writer.md",
    f"Security:\n{security_findings}\n\nCost:\n{cost_findings}")
```

---

## 7. Project Structure Template

```
agent-toolkit/
├── skills/                          # Methodology prompts (the "brains")
│   ├── security-scan.md             # Security audit methodology
│   ├── finops-analysis.md           # Cost analysis methodology
│   ├── code-review.md               # Code review methodology
│   └── discovery.md                 # Codebase discovery methodology
│
├── tools/                           # Python tool functions (the "hands")
│   ├── __init__.py
│   ├── file_reader.py               # read_file(path) → content
│   ├── file_search.py               # glob_search(pattern) → [paths]
│   ├── content_search.py            # grep(pattern, path) → [matches]
│   ├── command_runner.py            # run_command(cmd) → stdout (sandboxed)
│   └── git_inspector.py             # git_log(), git_diff(), git_ls_files()
│
├── agent/                           # Core agent machinery
│   ├── __init__.py
│   ├── loop.py                      # The agent loop (~50 lines)
│   ├── tool_executor.py             # Maps tool_call name → function
│   └── llm_client.py               # Ollama / vLLM / API client abstraction
│
├── reports/                         # Generated reports (gitignored)
│   └── .gitkeep
│
├── tests/                           # Test suite
│   ├── test_tools.py                # Tool unit tests
│   ├── test_loop.py                 # Agent loop tests (mocked LLM)
│   └── fixtures/                    # Test codebases for scanning
│       └── vulnerable-app/
│
├── run_security_scan.py             # CLI: python run_security_scan.py /path/to/project
├── run_finops_analysis.py           # CLI: python run_finops_analysis.py /path/to/project
├── requirements.txt                 # ollama, vllm (optional), rich (optional)
├── Dockerfile                       # Containerized agent for production
└── README.md
```

### Directory Purposes

| Directory | Contains | Changes How Often |
|-----------|----------|------------------|
| `skills/` | Markdown prompts — the methodology | Frequently (tuning prompts is the main work) |
| `tools/` | Python functions the LLM can call | Rarely (stable after initial build) |
| `agent/` | Loop, executor, LLM client | Very rarely (core infrastructure) |
| `reports/` | Output from agent runs | Every run (gitignored) |

### Key Insight: Prompt Engineering > Code Engineering

80% of development time should be spent refining `skills/*.md` files, not the agent infrastructure. The loop, tools, and executor are stable after initial build. The prompts are where quality comes from.

---

## 8. Build Sequence

Ordered learning path from zero to production agent.

### Step 1: Local LLM Setup

```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull models
ollama pull qwen2.5-coder:32b    # Primary coding model
ollama pull deepseek-r1:14b       # Thinking model (lighter weight)

# Verify
ollama run qwen2.5-coder:32b "Write a Python hello world"
```

### Step 2: Build Tool Functions

Start with `file_reader.py` and `content_search.py` — these cover 80% of use cases.

```bash
mkdir -p agent-toolkit/{tools,agent,skills,reports}
touch agent-toolkit/tools/__init__.py
```

Write each tool function with:
- Type hints on all parameters and returns
- Docstring matching the tool schema description
- Error handling (file not found, timeout, permission denied)
- Output size caps (truncate large results)

Test each tool independently:
```python
# Quick test
from tools.file_reader import read_file
print(read_file("tools/file_reader.py"))
```

### Step 3: Build the Agent Loop

Implement the loop from Section 5. Start with Ollama's Python client:

```python
import ollama

response = ollama.chat(
    model="qwen2.5-coder:32b",
    messages=messages,
    tools=tool_schemas
)
```

Test with a simple prompt: "Read the file at tools/file_reader.py and tell me what it does."

Verify the loop:
1. Sends the message to the LLM
2. LLM responds with a `read_file` tool call
3. Loop executes `read_file` and appends result
4. LLM responds with a text summary
5. Loop exits and returns the summary

### Step 4: Write Your First Skill (Secrets Detection Only)

Don't write a full security scanner yet. Start with just secrets detection:

```markdown
# Secrets Scanner

## Identity
You are a secrets detection specialist. Find hardcoded credentials in the codebase.

## Methodology
1. Search for secret patterns: grep('sk-|AKIA|ghp_|password\s*=|secret\s*=')
2. Check for .env files: glob_search('**/.env*')
3. Verify .env is in .gitignore: read_file('.gitignore')
4. Check git history: run_command('git log --all --diff-filter=A -- *.env *.pem *.key')

## Output Format
## Secrets Scan Results
| Severity | File | Line | Pattern | Finding |
|----------|------|------|---------|---------|
| ... | ... | ... | ... | ... |
```

### Step 5: Test and Iterate

Run your agent against a known-vulnerable test codebase:
1. Create `tests/fixtures/vulnerable-app/` with intentional vulnerabilities
2. Run the secrets scanner
3. Check: Did it find the planted secrets? Did it miss any? Did it false-positive?
4. Refine the skill prompt based on results

### Step 6: Expand the Skill

Add more phases to the security scan: injection detection, dependency checking, container analysis. Each phase is a new section in the skill prompt.

### Step 7: Build a Second Skill

Create `finops-analysis.md` — a completely different domain but the same infrastructure. This proves the architecture is reusable.

### Step 8: Multi-Agent (Optional)

If a single agent hits context window limits, split into:
1. Discovery Agent → produces codebase summary
2. Specialist Agent → uses summary + deep analysis
3. Report Agent → synthesizes findings

This is the same loop running three times with different skill prompts and context handoff between them.

---

## Summary

| Concept | Key Takeaway |
|---------|-------------|
| **Mental Model** | Skill.md → LLM → Tool Call → Execute → Loop → Output |
| **Stack** | Qwen-2.5-Coder-32B (code) + DeepSeek-R1 (thinking) + Ollama/vLLM |
| **Tools** | 5 core tools: read, glob, grep, command, git |
| **Skills** | Identity + Methodology + Output Format. Specificity is everything. |
| **Loop** | ~50 lines. Temperature 0.1. Truncate tool results. Cap turns. |
| **Production** | Multi-phase, model splitting, sandboxing, context management |
| **Build Order** | Ollama → tools → loop → simple skill → test → expand → second skill |

The architecture is intentionally simple. The complexity belongs in the skill prompts, not the infrastructure.
