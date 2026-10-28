import json
import os
import urllib.request

token = os.environ["KB_API_TOKEN"]
queries = [
    ("t17", "出差伙食补助的发放标准是多少？", "财务处小助手"),
    ("s08", "差旅", "财务处小助手"),
    ("c08", "出差期间伙食补助费按什么标准发放？", "财务处小助手"),
    ("t16", "科研经费单笔支出超过多少需要校领导审批？", "财务处小助手"),
    ("u29", "宿舍报修可以通过什么渠道？", "问道中传"),
    ("t08", "教学青年拔尖人才培育对象的年龄要求是多少？", None),
    ("t08a", "教学青年拔尖人才培育对象的年龄要求是多少？", "问道中传"),
    ("s02", "推免", None),
    ("a14", "校史馆团体参观要提前几天预约？", None),
    ("u08", "重修、重考取得的必修课学分最多认定多少？", None),
    ("u03", "哪些学生不得申请转专业？", "中传教务君"),
    ("u13", "三好学生多久评选一次？", "新生帮手"),
    ("g05", "博士学位答辩公告要提前几天公示？", None),
    ("c03", "辅修学士学位对平均学分绩点有什么要求？", "中传教务君"),
    ("c05", "博士学位答辩公告需要提前几天公示？", "问道中传"),
    ("c10", "校园卡丢失补办需要交多少工本费？", None),
    ("t02", "教授岗位聘用需要满足哪些条件？", None),
    ("n07", "2026年第10次校长办公会纪要内容是什么？", None),
    ("n03", "学校游泳馆年卡多少钱？", None),
    ("u17", "学生在校医院门诊看病需要自付多少？", "问道中传"),
    ("u21", "学费和住宿费怎么缴纳？", None),
    ("g04", "研究生提出学位申请前要完成哪些环节？", None),
    ("t18", "多少金额以上的修缮工程由校园建设处负责？", "中传小资"),
]
for case_id, query, assistant in queries:
    payload = {"query": query, "top_k": 5}
    if assistant:
        payload["assistant"] = assistant
    request = urllib.request.Request(
        "http://127.0.0.1:8000/v1/search",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
    )
    response = json.loads(urllib.request.urlopen(request, timeout=120).read())
    print("\n=====", case_id, "|", assistant, "| no_answer=", response["no_answer"],
          "conf=", response["confidence"])
    for rank, item in enumerate(response["results"], 1):
        citation = item["citation"]
        print("  %d. [%s] p%s-%s rerank=%s generic=%s" % (
            rank, citation["doc_title"][:58], citation.get("page_start"),
            citation.get("page_end"), item.get("rerank_score"), item.get("is_generic"),
        ))
