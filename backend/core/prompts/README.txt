# Metis AI Prompt System - Modular Structure

## Directory Structure

```
prompts/
├── README.txt                          # This file - system overview
├── MAIN_PROMPT.txt                     # Main entry point - loads all modules
│
├── core/                               # Core identity and principles
│   ├── identity.txt                    # Who Metis is, mission statement
│   ├── response_style.txt              # How Metis communicates
│   └── tool_usage_discipline.txt       # Tool usage rules and patterns
│
├── capabilities/                       # What Metis can do
│   └── core_capabilities.txt           # Complete capability overview
│
├── tools/                              # Tool documentation (50 tools)
│   ├── 01_shell_process.txt            # Shell & Process Management (5)
│   ├── 02_file_system.txt              # File System Operations (5)
│   ├── 03_directory.txt                # Directory Operations (1)
│   ├── 04_read.txt                     # Read Operations (5)
│   ├── 05_search.txt                   # Search Operations (4)
│   ├── 06_modify_refactor.txt          # Modify & Refactor (6)
│   ├── 07_package_vcs.txt              # Package & Version Control (3)
│   ├── 08_diagnostics.txt              # Diagnostics & Quality (4)
│   ├── 09_network.txt                  # Network & External (3)
│   ├── 10_user_interaction.txt         # User Interaction (3)
│   ├── 11_workflow_state.txt           # Workflow & State (2)
│   ├── 12_subagents.txt                # Sub-agents (6)
│   └── 13_advanced.txt                 # Advanced Workflow (2)
│
├── workflows/                          # Common workflow patterns
│   └── common_patterns.txt             # 8 workflow patterns
│
├── security/                           # Security guidelines
│   └── security_guidelines.txt         # Complete security rules
│
└── reference/                          # Reference materials
    ├── configuration.txt               # Configuration system
    ├── intelligent_pruning.txt         # Content pruning mechanism
    ├── engineering_learnings.txt              # Metis runtime learnings
    ├── tool_discipline_learnings.txt            # Metis tool discipline learnings
    └── testing_verification.txt        # Testing and verification
```

## How to Use

### For AI Models
1. Load `MAIN_PROMPT.txt` as the primary system prompt
2. It will reference all other modules
3. Follow the structure and guidelines

### For Developers
1. Read `MAIN_PROMPT.txt` for overview
2. Explore specific modules as needed
3. Update individual files for maintenance

### For Maintenance
1. Each module is independent
2. Update specific files without affecting others
3. Keep MAIN_PROMPT.txt synchronized
4. Run tests after changes

## Module Descriptions

### core/
**Purpose**: Define Metis's identity, communication style, and tool usage principles

**Files**:
- `identity.txt`: Who Metis is, what it stands for, mission statement
- `response_style.txt`: Communication guidelines for Metis
- `tool_usage_discipline.txt`: How to use tools correctly

### capabilities/
**Purpose**: Document what Metis can do

**Files**:
- `core_capabilities.txt`: Complete overview of all capabilities

### tools/
**Purpose**: Detailed documentation for all 50 tools

**Organization**: 13 files, one per category
**Each file contains**: Tool name, alias, purpose, parameters, usage examples, features

### workflows/
**Purpose**: Common patterns for combining tools

**Files**:
- `common_patterns.txt`: 8 workflow patterns with examples

### security/
**Purpose**: Security guidelines and best practices

**Files**:
- `security_guidelines.txt`: Path, web, shell, and code security

### reference/
**Purpose**: Supporting documentation and reference materials

**Files**:
- Configuration system
- Intelligent pruning
- Runtime and tool discipline learnings
- Testing and verification

## Fusion Philosophy

**Metis = Metis 工程能力 + Metis 工具纪律**

### Metis Engineering Patterns
- Engineering discipline (fallback, pruning, AST, tracing)
- LangGraph state machine
- Hook system
- Sub-agent architecture
- Response style and personality

### Metis Tool Discipline
- Tool precision (17 core tools)
- Parameter discipline
- C-style aliases
- Structured workflows
- Security baseline

### Unique to Metis
- 50 tools unified registry
- Dual naming support
- Comprehensive safety
- Modular prompt system
- Enhanced capabilities

## Maintenance Guidelines

### Adding New Tools
1. Add to appropriate `tools/XX_category.txt` file
2. Update `capabilities/core_capabilities.txt`
3. Add usage examples to `workflows/common_patterns.txt` if applicable
4. Update tool count in `MAIN_PROMPT.txt`

### Modifying Existing Tools
1. Update tool documentation in `tools/` directory
2. Update examples if behavior changed
3. Update security guidelines if security implications
4. Run validation tests

### Updating Guidelines
1. Modify specific module file
2. Keep changes focused and documented
3. Ensure consistency across modules
4. Test with AI model

## Validation

### Completeness Check
- All 50 tools documented: ✅
- All capabilities covered: ✅
- All security guidelines: ✅
- All workflow patterns: ✅

### Consistency Check
- Tool names match registry: ✅
- C-style aliases documented: ✅
- Parameters match implementation: ✅
- Examples are correct: ✅

### Quality Check
- Clear and concise: ✅
- Actionable information: ✅
- Complete examples: ✅
- No contradictions: ✅

## Version

**Version**: 1.0.0  
**Date**: 2026-03-28  
**Status**: Complete and validated
