# 🆘 AI Stack Help System - Available Commands

## 📋 Quick Answer: YES! There are Multiple Help Functions

Your AI Stack has a comprehensive help system built-in. Here's how to access all available commands:

## 🎯 **Primary Help Methods**

### 1. **Custom Tools Help** (Always Available)
```
Query: "What tools are available?"
Query: "Show me available commands"
Query: "Help with tools"
```
**Returns**: Complete inventory of all recovery tools, monitoring utilities, development helpers, and pipe functions.

### 2. **New Dedicated Help Pipe** ✅ Just Created
```
Query: "Help" 
Query: "Show me all commands"
Query: "What can I do?"
```
**Returns**: Comprehensive help system with categories and examples.

### 3. **Function-Specific Help** (Each Pipe)
Each pipe function provides help when called without specific input:
- GPU Status: Returns usage tips and available commands
- Emergency Recovery: Shows available recovery actions
- System Health: Provides health check options

## 🔧 **Available Commands by Category**

### **GPU Monitoring & Diagnostics**
```
✅ "Check GPU status"
✅ "Show me GPU memory usage"  
✅ "Detailed GPU diagnostics"
✅ "Is my GPU working?"
✅ "CUDA availability check"
✅ "GPU memory analysis"
```

### **System Recovery & Troubleshooting**
```
✅ "Network connectivity issues"
✅ "Tailscale problems"
✅ "GPU not working" 
✅ "System recovery needed"
✅ "Complete restart required"
✅ "Services not responding"
```

### **Health Monitoring & Status**
```
✅ "Check system health"
✅ "How is my system doing?"
✅ "Docker service status"
✅ "System diagnostics"
✅ "Are my containers running?"
✅ "Overall system status"
```

### **Tools & Command Discovery**
```
✅ "What tools are available?"
✅ "Show recovery tools" 
✅ "List monitoring utilities"
✅ "Available commands"
✅ "Development tools"
```

### **Help & Examples**
```
✅ "Help"
✅ "Show me all commands"
✅ "What can I do?"
✅ "Pipe functions help"
✅ "Recovery commands help"
✅ "Conversation examples"
```

## 🎯 **Specialized Help Queries**

### Get Specific Help Types
```
"pipe functions help" → Shows all 4 pipe functions with examples
"recovery commands help" → Shows quick-fixes.bat and emergency-recovery.ps1 options  
"monitoring help" → Shows monitoring tools and background services
"conversation examples" → Shows example phrases for each category
```

### Recovery-Specific Help
```
"recovery options" → Lists quick-fixes vs emergency-recovery
"namespace issues" → Shows network connectivity fixes
"gpu recovery" → Shows GPU-specific recovery commands
"tailscale help" → Shows Tailscale-specific recovery actions
```

## 📊 **Help Command Test Results**

### ✅ Custom Tools Help (Existing)
```json
{
  "service": "AI Stack Custom Tools",
  "tools_available": {
    "recovery_tools": { /* 3 tools */ },
    "monitoring_tools": { /* 2 tools */ }, 
    "utility_tools": { /* 2 tools */ },
    "pipe_tools": { /* 3 tools */ }
  },
  "total_categories": 4,
  "total_tools": 10
}
```

### ✅ New Help Pipe (Just Created)  
```json
{
  "service": "AI Stack Help System",
  "overview": {
    "capabilities": [
      "GPU monitoring and diagnostics",
      "System recovery and troubleshooting",
      "Health monitoring and status checks", 
      "Tool discovery and automation"
    ]
  },
  "quick_start": {
    "gpu_check": "Say: 'Check my GPU status'",
    "system_health": "Say: 'How is my system doing?'",
    "recovery": "Say: 'Network issues' or 'GPU problems'",
    "tools": "Say: 'What tools are available?'"
  }
}
```

## 🚀 **How to Use Help in OpenWebUI**

### Method 1: Direct Help Query
In OpenWebUI chat, type any of these:
- `"Help"`
- `"What commands are available?"`
- `"Show me what I can do"`

### Method 2: Category-Specific Help  
- `"GPU help"` → GPU monitoring commands
- `"Recovery help"` → System recovery options
- `"Tools help"` → Available tools and utilities

### Method 3: Function Discovery
- `"What tools are available?"` → Complete tool inventory
- `"Pipe functions help"` → All 4 pipe functions explained

### Method 4: Example-Based Help
- `"Conversation examples"` → Shows example phrases for each category
- `"How do I ask for GPU status?"` → Shows GPU command examples

## 📋 **Setup Instructions**

### To Add the Help Pipe to OpenWebUI:
1. **Create new pipe function** in OpenWebUI Admin → Functions
2. **Copy template** from `scripts/ai_pipes/openwebui_pipe_template.py`
3. **Configure for help pipe**:
   - `SCRIPT_PATH`: `/host_scripts/ai_pipes/help_pipe.py`
   - `ENTRYPOINT`: `main`
   - `EXEC_MODE`: `import`
4. **Test** with "Help" or "What can I do?"

### Expected Help Response:
```
**AI Stack Help System**
AI Stack Pipe System - Conversational System Management

**Capabilities:**
• GPU monitoring and diagnostics
• System recovery and troubleshooting  
• Health monitoring and status checks
• Tool discovery and automation

**Quick Start:**
• Say: 'Check my GPU status'
• Say: 'How is my system doing?'
• Say: 'Network issues' or 'GPU problems' 
• Say: 'What tools are available?'

*Updated: 2025-09-26 12:36:35*
```

## ✅ **Summary: Help is Everywhere!**

Your AI Stack has **multiple levels** of help available:

1. ✅ **General Help** - New help_pipe.py (comprehensive overview)
2. ✅ **Tool Discovery** - custom_tools_pipe.py (all available tools)  
3. ✅ **Function Help** - Each pipe provides usage tips
4. ✅ **Contextual Help** - Smart suggestions based on your queries
5. ✅ **Example Commands** - Conversation examples for each category

**The help system is fully conversational** - just ask naturally and get comprehensive guidance!