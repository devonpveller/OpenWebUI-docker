import sqlite3, re
c=sqlite3.connect('/app/backend/data/webui.db')
t=c.execute("select content from tool where id='fileshed'").fetchone()[0]
f=c.execute("select content from function where id='copy_research_note'").fetchone()[0]
print("fileshed: await Groups. =", len(re.findall(r'await Groups\.',t)),
      "| bare-unawaited =", len(re.findall(r'(?<!await )\bGroups\.[a-z_]+\(',t)))
print("copy_research_note: await Chats/Notes =", len(re.findall(r'await (?:Chats|Notes)\.',f)),
      "| bare-unawaited =", len(re.findall(r'(?<!await )\b(?:Chats|Notes)\.(?:get_chat_by_id|get_note_by_id)\(',f)))
