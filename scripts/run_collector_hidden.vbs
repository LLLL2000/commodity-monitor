' Launches run_collector.cmd with no visible window (hidden background process).
' Used by the CommodityMonitorCollector scheduled task (at logon).
Set sh = CreateObject("WScript.Shell")
sh.Run "cmd /c ""C:\Users\consu\Documents\Commodity_Monitoring\commodity-monitor\scripts\run_collector.cmd""", 0, False
