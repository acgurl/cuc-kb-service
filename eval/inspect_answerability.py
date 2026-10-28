import json
import os
import urllib.request

token = os.environ["KB_API_TOKEN"]
queries = [
    ("u29", "宿舍报修可以通过什么渠道？", "问道中传"),
    ("u17", "学生在校医院门诊看病需要自付多少？", "问道中传"),
    ("n03", "学校游泳馆年卡多少钱？", None),
    ("n07", "2026年第10次校长办公会纪要内容是什么？", None),
    ("n08", "北京市大学生医保个人缴费标准是多少？", None),
    ("n01", "中国传媒大学附属小学的招生划片范围是什么？", None),
    ("n02", "学校教职工体检套餐价格是多少？", None),
    ("n04", "校车发车时刻表在哪里查？", None),
    ("n05", "食堂今天的菜单是什么？", None),
    ("n06", "校园理发店剪一次头发多少钱？", None),
    ("n09", "学校洗衣房洗一次衣服多少钱？", None),
    ("n10", "学校幼儿园入园报名需要什么条件？", None),
    ("u13", "三好学生多久评选一次？", "新生帮手"),
    ("g05", "博士学位答辩公告要提前几天公示？", None),
    ("c05", "博士学位答辩公告需要提前几天公示？", "问道中传"),
    ("t19", "实验室安全风险分几级，哪一级要报教育部备案？", "中传小资"),
    ("g24", "校领导班子成员指导研究生数量有什么限制？", "问道中传"),
    ("a13", "2026年团学系统春季评优在什么时间表彰？", "团委小助手"),
    ("u23", "校园无线网的SSID是什么？", None),
    ("u21", "学费和住宿费怎么缴纳？", None),
    ("t18", "多少金额以上的修缮工程由校园建设处负责？", "中传小资"),
    ("u08", "重修、重考取得的必修课学分最多认定多少？", None),
    ("u10", "本科生国家奖学金一年多少钱？", "新生帮手"),
    ("u24", "校园卡丢失补办要交多少钱？", None),
    ("u25", "图书馆阅览区每天开放到几点？", None),
    ("u26", "2026级新生什么时候报到？", None),
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
    top = response["results"][0]["citation"]["doc_title"][:52] if response["results"] else None
    print("%-4s no_answer=%-5s conf=%-6s ans=%-40s top=%s" % (
        case_id, response["no_answer"], response["confidence"],
        json.dumps(response.get("answerability"), ensure_ascii=False), top,
    ))
