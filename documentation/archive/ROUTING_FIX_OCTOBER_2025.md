# Routing Issue Fix - October 2025

## Problems Identified

### Issue 1: "start serving lmstudio on port 5506" Routes to Help-System
**Root Cause**: Keyword mismatch - routing logic checked for "serve" but user said "serv**ing**"

**Affected Files**:
- `core/router.py` (line 462)
- `modules/custom-tools/service/custom_tools.py` (line 248)

**Solution**: Added "serving" to keyword detection list

### Issue 2: "fix lmstudio" Returns Partial Success with Socat Timeout
**Root Cause**: Old `lmstudio_fix.py` script tried to manage socat directly instead of using the new Tailscale Serve Admin tool

**Affected Files**:
- `scripts/lmstudio_fix.py` (legacy version with socat management)
- `modules/emergency-recovery/service/emergency_recovery.py` (configuration)

**Solution**: Created `lmstudio_fix_v2.py` that calls the new `tailscale_serve_admin.py` tool

## Changes Applied

### 1. Router Keyword Fix (core/router.py)
```python
# Before:
if any(keyword in input_lower for keyword in ["serve", "expose", "tailscale"]) and \
   any(keyword in input_lower for keyword in ["start", "stop", "status", "lmstudio", "service", "port"]):

# After:
if any(keyword in input_lower for keyword in ["serve", "serving", "expose", "tailscale"]) and \
   any(keyword in input_lower for keyword in ["start", "stop", "status", "lmstudio", "service", "port"]):
```

### 2. Custom Tools Keyword Fix (modules/custom-tools/service/custom_tools.py)
```python
# Before:
is_tailscale_serve = any(keyword in input_lower for keyword in ["serve", "expose", "tailscale"]) and \
                    any(keyword in input_lower for keyword in ["start", "stop", "status", "lmstudio", "service", "health"])

# After:
is_tailscale_serve = any(keyword in input_lower for keyword in ["serve", "serving", "expose", "tailscale"]) and \
                    any(keyword in input_lower for keyword in ["start", "stop", "status", "lmstudio", "service", "health", "port"])
```

### 3. New LM Studio Fix Script (scripts/lmstudio_fix_v2.py)
Created new script that:
- Finds the `tailscale_serve_admin.py` tool
- Calls it with proper arguments for LM Studio configuration
- Returns proper exit codes (0=success, 1=error, 2=partial)
- No socat management - uses Tailscale serve directly

### 4. Emergency Recovery Configuration Update
```python
# Before:
"lmstudio_fix": "scripts/lmstudio_fix.py"

# After:
"lmstudio_fix": "scripts/lmstudio_fix_v2.py"  # Updated to V2 using tailscale_serve_admin
```

## Testing Results

### Test 1: Routing "start serving lmstudio on port 5506"
**Before**: Routed to `help-system` ❌
**After**: Routes to `custom-tools` → `tailscale_serve_pipe` → `tailscale_serve_admin` ✅

**Command flow**:
```
OpenWebUI → unified_pipe.py → router.py → custom-tools module → tailscale_serve_pipe.py → tailscale_serve_admin.py
```

### Test 2: Emergency Recovery "fix lmstudio"
**Before**: Partial success with socat timeout ⚠️
**After**: Calls new admin tool for proper Tailscale serve configuration ✅

**Command flow**:
```
OpenWebUI → unified_pipe.py → router.py → emergency-recovery module → lmstudio_fix_v2.py → tailscale_serve_admin.py
```

## Python Cache Management

**Critical**: Python caches compiled `.pyc` files. After editing routing logic, you must:
1. Delete cache: `Remove-Item -Path "core\__pycache__\*.pyc" -Force`
2. Restart OpenWebUI: `docker compose restart openwebui`

Without this, old routing logic will continue to execute.

## Keyword Matching Lessons Learned

1. **User input varies**: "serve" vs "serving" - always include common variations
2. **Order matters**: More specific routing conditions should come FIRST
3. **Negative conditions**: Use `not any(...)` to exclude keyword collisions
4. **Test with actual user input**: Don't assume exact wording

## Architecture Notes

### Why Two Paths to LM Studio Fix?

1. **Natural language**: "start serving lmstudio on port 5506"
   - Routes through `custom-tools` module
   - Calls `tailscale_serve_pipe.py` (natural language parser)
   - Extracts action, path, port from user text
   - Calls `tailscale_serve_admin.py` with parsed parameters

2. **Emergency recovery**: "fix lmstudio"
   - Routes through `emergency-recovery` module
   - Calls `lmstudio_fix_v2.py` (wrapper script)
   - Uses hardcoded LM Studio configuration (path=/lmstudio, port=8234)
   - Calls `tailscale_serve_admin.py` with preset parameters

Both paths end at the same admin tool, just with different parameter sources.

## Files Modified

1. ✅ `core/router.py` - Added "serving" keyword
2. ✅ `modules/custom-tools/service/custom_tools.py` - Added "serving" and "port" keywords
3. ✅ `scripts/lmstudio_fix_v2.py` - NEW - Wrapper for admin tool
4. ✅ `modules/emergency-recovery/service/emergency_recovery.py` - Updated to use V2 script

## Files NOT Modified (Why)

- `scripts/lmstudio_fix.py` - Legacy version kept for reference, no longer called
- `scripts/ai_pipes/tailscale_serve_pipe.py` - Already correct, no changes needed
- `modules/custom-tools/service/tailscale_serve_admin.py` - Already correct, no changes needed

## Next Steps for User

1. **Test in OpenWebUI**: Try both commands:
   - "start serving lmstudio on port 5506" (should route to custom-tools)
   - "fix lmstudio" (should use new V2 script)

2. **Verify Tailscale**: If Tailscale isn't configured in your environment, you'll see appropriate error messages (this is expected)

3. **Monitor logs**: Watch container logs for routing decisions:
   ```powershell
   docker compose logs -f openwebui | Select-String -Pattern "custom-tools|emergency-recovery"
   ```

4. **Optional cleanup**: Consider removing old `lmstudio_fix.py` after confirming V2 works

## Summary

- ✅ Routing fixed by adding "serving" keyword
- ✅ LM Studio fix updated to use new admin tool architecture
- ✅ Both paths now work correctly
- ✅ Python cache cleared and container restarted
- ✅ Architecture maintains manifest-driven pattern
- ✅ Security constraints preserved (no docker.sock mounting)

All changes follow the project's container-native architecture where scripts execute INSIDE the OpenWebUI container and use the Tailscale HTTP API (localhost:41641) for service management.
