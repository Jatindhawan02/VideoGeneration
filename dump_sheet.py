import phone, yt_flow
d = phone.connect()
try:
    print("Screen pe ye dikh raha hai:")
    for n in yt_flow.nodes(d):
        s = (n["text"] or n["desc"]).replace("\n", " ")
        if s:
            print("  -", s[:60], "| id:", n["id"].split("/")[-1])
    yt_flow.dump(d, "export_sheet")
finally:
    d.quit()
