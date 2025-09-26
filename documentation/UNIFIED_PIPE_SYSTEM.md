# 🚀 AI Stack Unified Pipe System - Migration Guide

## Overview

The unified pipe system replaces **multiple individual pipe functions** with a **single intelligent router** that provides access to all AI Stack capabilities through one OpenWebUI function.

## 🎯 Key Benefits

### Before (Multiple Functions)
❌ **4-5 separate pipe functions** in OpenWebUI Admin  
❌ **Manual selection** of which function to use  
❌ **Duplicate management** and maintenance overhead  
❌ **User confusion** about which function to choose  
❌ **Complex setup** with multiple configurations  

### After (Unified Router)  
✅ **Single pipe function** in OpenWebUI  
✅ **Intelligent routing** based on natural language  
✅ **Unified management** and maintenance  
✅ **Simple user experience** - just ask naturally  
✅ **Easy setup** with one configuration  

## 🔧 Migration Instructions

### Step 1: Remove Old Pipe Functions (If Any)
1. Go to **OpenWebUI Admin → Functions**
2. **Delete** any existing AI Stack pipe functions:
   - GPU Status Pipe
   - Emergency Recovery Pipe  
   - System Health Pipe
   - Custom Tools Pipe
   - Help Pipe

### Step 2: Install Unified Pipe
1. **Create new function** in OpenWebUI Admin → Functions
2. **Copy entire content** from `scripts/ai_pipes/unified_openwebui_pipe.py`
3. **Paste as function code**
4. **Name**: "AI Stack Unified System" (or preferred name)
5. **Save function**

### Step 3: Test Installation
Test with these queries in OpenWebUI:
```
"Router status"           → Shows loaded modules
"Check GPU status"        → Routes to GPU monitoring
"Network issues"          → Routes to recovery system  
"System health"           → Routes to health monitoring
"What tools available?"   → Routes to tool discovery
"Help"                    → Routes to help system
```

## 🎭 Intelligent Routing Examples

### GPU Monitoring Queries → `gpu_status_pipe.py`
- "Check my GPU status"
- "Is CUDA available?"
- "GPU memory usage"
- "RTX 3090 diagnostics"
- "PyTorch GPU check"

### Recovery & Troubleshooting → `emergency_recovery_pipe.py`
- "Network connectivity issues"
- "Tailscale is down"
- "System recovery needed"
- "Services not responding"
- "Fix my system"

### System Health → `system_health_pipe.py`
- "How is my system doing?"
- "Docker container status"
- "Service health check"
- "System diagnostics"
- "Monitor system"

### Tool Discovery → `custom_tools_pipe.py`
- "What tools are available?"
- "Show me available commands"
- "Available utilities"
- "Tool discovery"

### Help & Guidance → `help_pipe.py`
- "Help"
- "What can I do?"
- "Available commands"
- "Conversation examples"

## 🔍 Router Algorithm

### Confidence Scoring System
```python
# Phrase matches (exact): 0.7 weight
# Keyword matches (partial): 0.5 weight  
# Pattern confidence multiplier: 0.75-0.95
# Minimum threshold: 0.15
```

### Priority Order (High to Low)
1. **GPU Status** (0.95 confidence) - Highest specificity
2. **Emergency Recovery** (0.9 confidence) - Problem-solving priority
3. **System Health** (0.85 confidence) - Monitoring focus
4. **Tool Discovery** (0.8 confidence) - Utility functions
5. **Help System** (0.75 confidence) - General guidance

### Fallback Behavior
- **No match** (< 0.15 confidence) → Routes to help system
- **Empty input** → Shows router status

## 📊 Router Status Information

The router provides comprehensive status information:

```json
{
  "service": "AI Stack Unified Router",
  "status": "operational", 
  "loaded_modules": {
    "gpu_status": "GPU monitoring and diagnostics",
    "emergency_recovery": "System recovery and troubleshooting",
    "system_health": "Health monitoring and status checks", 
    "custom_tools": "Tool discovery and automation",
    "help": "Help system and command discovery"
  },
  "module_count": 5,
  "capabilities": [
    "GPU monitoring and diagnostics",
    "System recovery and troubleshooting",
    "Health monitoring and status checks", 
    "Tool discovery and automation",
    "Help system and command guidance"
  ]
}
```

## 🛠️ Technical Architecture

### File Structure
```
scripts/ai_pipes/
├── ai_stack_router.py           # Main router logic
├── unified_openwebui_pipe.py    # OpenWebUI integration
├── gpu_status_pipe.py           # GPU monitoring (unchanged)
├── emergency_recovery_pipe.py   # Recovery system (unchanged) 
├── system_health_pipe.py        # Health monitoring (unchanged)
├── custom_tools_pipe.py         # Tool discovery (unchanged)
└── help_pipe.py                 # Help system (unchanged)
```

### Integration Pattern
```
OpenWebUI User Query
        ↓
unified_openwebui_pipe.py (OpenWebUI interface)
        ↓
ai_stack_router.py (Intelligent routing)
        ↓
Appropriate individual pipe function
        ↓
Formatted response back to user
```

### Routing Metadata
Every routed response includes metadata:
```json
"_router_info": {
  "routed_to": "gpu_status",
  "confidence": 1.38,
  "description": "GPU monitoring and diagnostics",
  "router_timestamp": "2025-09-26 12:44:34"
}
```

## 🚦 Troubleshooting

### Router Not Loading Modules
**Symptom**: "Module not found" or "0 modules loaded"  
**Solution**: Verify `/host_scripts/ai_pipes/` mount and file permissions

### Incorrect Routing  
**Symptom**: GPU query goes to help instead of GPU module  
**Solution**: Check keyword matching - add specific terms to routing patterns

### OpenWebUI Function Error
**Symptom**: Function fails to execute  
**Solution**: Verify `unified_openwebui_pipe.py` is complete and properly pasted

### Debug Mode
Enable verbose logging in OpenWebUI function:
```python
DEBUG_MODE: bool = Field(default=True)
```

## 🎯 Usage Tips

### Best Practices
- **Be specific** in queries for better routing accuracy
- **Use natural language** - the router handles conversational input
- **Check router status** with empty query or "router status"
- **Include context** for complex issues (e.g., "Tailscale network down")

### Query Optimization
- ✅ "Check my RTX GPU status" → High confidence GPU routing
- ✅ "Tailscale connectivity issues" → High confidence recovery routing  
- ✅ "Docker service health check" → High confidence health routing
- ❌ "Status" → Too generic, may route to help

## 📈 Performance Benefits

### Reduced Complexity
- **Before**: 4-5 functions × configuration overhead = High complexity
- **After**: 1 function × unified configuration = Low complexity

### Better User Experience  
- **Before**: User must choose correct function manually
- **After**: System intelligently routes based on natural language

### Easier Maintenance
- **Before**: Update routing logic in 4-5 separate functions
- **After**: Update routing logic in single router file

### Improved Accuracy
- **Centralized routing logic** provides consistent behavior
- **Confidence scoring** ensures best match selection
- **Fallback mechanisms** prevent routing failures

## 🔄 Rollback Plan

If needed, you can easily rollback:

1. **Keep old individual pipe files** (unchanged)
2. **Recreate individual OpenWebUI functions** using original template
3. **Delete unified function**
4. **Restore previous workflow**

The unified system is **fully backward compatible** - all individual pipe functions remain functional for direct execution.

---

## 🎉 Summary

The unified pipe system transforms your AI Stack from a **complex multi-function setup** into a **streamlined single-access interface** while maintaining all existing functionality and adding intelligent routing capabilities.

**Next Step**: Follow the migration instructions above to consolidate your pipe functions!