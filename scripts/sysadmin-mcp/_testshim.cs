// Test-only PATH recorder for sysadmin-mcp test runs (_testguard.py compiles it with the .NET
// Framework csc.exe into %TEMP%\acsr-shim-<hash>\docker.exe, copied as wsl.exe / schtasks.exe /
// docker-compose.exe, and puts that directory first on PATH). A NON-Python child that calls one of
// these tools by name lands here: the attempt is appended to ACSR_CALL_LOG as kind "shim" and the
// process exits 99 without doing anything.
using System;
using System.IO;

class AcsrShim {
    static string Esc(string s) { return s.Replace("\\", "\\\\").Replace("\"", "\\\""); }
    static int Main(string[] args) {
        string tool = Path.GetFileNameWithoutExtension(Environment.GetCommandLineArgs()[0]);
        string log = Environment.GetEnvironmentVariable("ACSR_CALL_LOG");
        if (!string.IsNullOrEmpty(log)) {
            try {
                File.AppendAllText(log, "{\"kind\":\"shim\",\"verdict\":\"refused\",\"tool\":\"" + Esc(tool)
                    + "\",\"argv\":[\"" + Esc(tool) + "\",\"" + Esc(string.Join(" ", args)) + "\"]}\n");
            } catch (Exception) { }
        }
        Console.Error.WriteLine("acsr test shim: '" + tool + "' is not reachable from a sysadmin test run");
        return 99;
    }
}
