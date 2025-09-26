# 🚀 How to Use the AI Stack Unified Pipe Function

## ✅ Setup Complete - Now Ready to Use!

Your AI Stack now has **ONE single pipe function** that intelligently routes to all your system capabilities. No more managing multiple separate functions!

## 🎯 **How It Works**

1. **Type natural language** in OpenWebUI chat
2. **Router analyzes** your request automatically  
3. **Routes to correct function** (GPU, Recovery, Health, Tools, Help)
4. **Returns formatted response** instead of raw JSON

## 📝 **Example Queries & Results**

### 🎮 **GPU Monitoring**
**Ask**: `"Check my GPU status"`
**You Get**:
```
🎮 GPU Status

CUDA: ✅ Available
PyTorch: 2.5.1+cu121 (CUDA 12.1)
Device Count: 1

GPU 0: NVIDIA GeForce RTX 3090 Ti
Memory: 24.0 GB free / 24.0 GB total
Allocated: 0.0 GB

Status: GPU functioning normally

Optimization Tips:
• Monitor memory usage during model loading
• Use torch.cuda.empty_cache() to free unused memory
• Consider using mixed precision for better performance
```

### 🔧 **System Recovery**  
**Ask**: `"Tailscale is down"` or `"Network issues"`
**You Get**:
```
🔧 System Recovery

Action: Tailscale
Description: Standard Tailscale recovery
Command: scripts\emergency-recovery.ps1 -Action recover
Urgency: Medium
Success Probability: High

Analysis: Detected issue type: tailscale

Next Steps:
• Review the suggested command
• Ensure you understand the impact  
• Execute the command in PowerShell/Command Prompt
• Monitor system status after execution

⚠️ Warning: Always review commands before execution
```

### 📊 **System Health**
**Ask**: `"How is my system doing?"` or `"System health check"`
**You Get**:
```
📊 System Health

Overall Status: 🟡 Fair (60/100)

Quick Status:
• Docker: ❌
• Services: ⚠️
• GPU: ✅

Issues Found:
• Docker not available

Recommendations:
• Start Docker Desktop or check Docker service

GPU Details: CUDA Available (1 devices)
```

### 🤖 **Router Status**
**Ask**: `"Router status"` or just send empty message
**You Get**:
```
🤖 AI Stack Unified System

Status: Operational
Modules Loaded: 5

Available Capabilities:
• GPU monitoring and diagnostics
• System recovery and troubleshooting
• Health monitoring and status checks
• Tool discovery and automation
• Help system and command guidance

Loaded Modules:
• gpu_status: GPU monitoring and diagnostics
• emergency_recovery: System recovery and troubleshooting
• system_health: Health monitoring and status checks
• custom_tools: Tool discovery and automation
• help: Help system and command discovery
```

## 🗣️ **Natural Language Examples**

### Instead of Raw JSON, Ask Naturally:

❌ **Old way**: Select specific pipe function, get JSON response
✅ **New way**: Just chat naturally!

**GPU Questions**:
- "Is my GPU working?"
- "CUDA availability check"
- "GPU memory status"
- "Check RTX performance"

**Recovery Issues**:
- "Network connectivity problems"
- "Services not responding"
- "System recovery needed"
- "GPU problems"

**Health Monitoring**:
- "System diagnostics"
- "Docker container status"
- "Overall system health"

**Getting Help**:
- "Help"
- "What can I do?"
- "Available commands"
- "Show me examples"

## 🎛️ **Router Intelligence**

The router uses **confidence scoring** to pick the best function:

- **High confidence** (0.7+): Direct routing to specific function
- **Medium confidence** (0.4-0.7): Routes with explanation
- **Low confidence** (<0.4): Routes to help system

**You can see routing decisions** in the container logs:
```
🎯 Routing to gpu_status (confidence: 0.71) for input: 'Check my GPU status...'
✅ Successfully routed to gpu_status
```

## 🛠️ **Troubleshooting**

### If You Get Raw JSON Instead of Formatted Response:
1. **Check the pipe function** was pasted completely
2. **Verify the router script** exists at `/host_scripts/ai_pipes/ai_stack_router.py`  
3. **Enable debug mode** by setting `DEBUG_MODE: true` in pipe function valves

### If Routing Seems Wrong:
- **Be more specific** in your queries
- **Use keywords** the router recognizes (GPU, network, system, etc.)
- **Check confidence scores** in container logs

### If Function Fails:
- **Check container logs**: `docker compose logs openwebui`
- **Verify volume mount**: `/host_scripts` should be accessible
- **Test router directly**: `docker compose exec openwebui python /host_scripts/ai_pipes/ai_stack_router.py`

## 🎉 **Benefits You Now Have**

✅ **Single Access Point**: One pipe function instead of 4-5
✅ **Natural Conversation**: No more selecting specific functions  
✅ **Formatted Responses**: Pretty output instead of raw JSON
✅ **Intelligent Routing**: Automatic function selection
✅ **Error Handling**: Clear error messages and suggestions
✅ **Comprehensive Help**: Built-in guidance and examples

## 🔄 **Next Steps**

1. **Copy the unified pipe function** from `scripts/ai_pipes/unified_openwebui_pipe.py` 
2. **Paste into OpenWebUI** Admin → Functions
3. **Delete old individual functions** (if any)
4. **Start chatting naturally** with your AI Stack!

Your system is now **much easier to use** while maintaining all the powerful functionality you had before. Just chat naturally and let the router handle the complexity!

---

**Quick Test**: Try asking `"Check my GPU status"` in OpenWebUI and you should get a beautifully formatted response instead of JSON!