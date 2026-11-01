#!/usr/bin/env python3
"""Build the expanded no-answer set used to calibrate KB_RERANK_MIN_SCORE.

Each question carries probe terms; every probe must have zero hits in the active
corpus (whitespace-normalized), otherwise the case is dropped and reported
(drafts are kept in CASES so the drop log documents why). Cases
are grouped by category so rejection can be reported per kind of question:

    campus   校园生活设施/服务，知识库没有收录
    adjacent 贴近已收录制度，但问的具体信息不在文档里（难负例）
    external 校外机构 / 他校 / 国家层面的政策
    instance 具体某次会议、某期、某个人的实时或个别信息
    personal 个人状态查询（成绩、余额、进度），只能在业务系统查
    offtopic 与学校无关

    python eval/heldout/build_noanswer.py
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# (id, category, query, probes)
CASES = [
    # campus
    ("na01", "campus", "学校里的快递驿站晚上几点关门？", ["快递驿站", "菜鸟驿站"]),
    ("na02", "campus", "宿舍楼下的自动售货机坏了吞钱找谁退？", ["售货机"]),
    ("na03", "campus", "电动车在学校哪里可以充电？", ["充电桩", "电动车充电"]),
    ("na04", "campus", "学校篮球场晚上需要预约吗？", ["篮球场"]),
    ("na05", "campus", "校园里有咖啡店吗，营业到几点？", ["咖啡店", "咖啡厅", "星巴克"]),
    ("na06", "campus", "眼镜坏了学校附近有配眼镜的地方吗？", ["配眼镜", "眼镜店"]),
    ("na07", "campus", "学校里哪里有工商银行的ATM机？", ["ATM", "取款机"]),
    ("na08", "campus", "在食堂丢了耳机去哪里认领？", ["失物招领"]),
    ("na09", "campus", "学校的羽毛球馆怎么订场？", ["羽毛球馆"]),
    ("na10", "campus", "校园里能骑共享单车进来吗？", ["共享单车"]),
    ("na11", "campus", "期末周通宵自习室在哪？", ["通宵自习"]),
    ("na12", "campus", "学校超市能用支付宝吗？", ["支付宝"]),
    ("na13", "campus", "宿舍可以用吹风机吗，功率限制多少瓦？", ["吹风机"]),
    ("na14", "campus", "校园里有流浪猫救助社团吗？", ["流浪猫"]),
    # adjacent (hard negatives)
    ("na15", "adjacent", "四六级考试的报名费是多少钱？", ["四六级报名费", "四级报名费", "六级报名费"]),
    ("na16", "adjacent", "教师资格证面试学校统一组织培训吗？", ["教师资格证面试", "教资面试"]),
    ("na17", "adjacent", "普通话水平测试在学校哪里报名？", ["普通话水平测试", "普通话测试"]),
    ("na18", "adjacent", "计算机二级考试学校是考点吗？", ["计算机二级", "计算机等级考试"]),
    ("na19", "adjacent", "研究生宿舍可以申请换成单人间吗？", ["单人间"]),
    ("na20", "adjacent", "校医院看牙可以镶牙吗，费用多少？", ["镶牙", "种植牙"]),
    ("na21", "adjacent", "本科生毕业照统一拍摄是哪天？", ["毕业照"]),
    ("na22", "adjacent", "留学生的汉语言补习班怎么报名？", ["汉语补习", "汉语言补习"]),
    ("na23", "adjacent", "新生军训服装尺码报错了能换吗？", ["军训服", "迷彩服"]),
    ("na24", "adjacent", "教职工子女上北京市重点高中学校有指标吗？", ["重点高中"]),
    ("na25", "adjacent", "老师出国开会可以坐商务舱吗？", ["商务舱"]),
    ("na26", "adjacent", "实验室的3D打印机怎么预约使用？", ["3D打印"]),
    ("na27", "adjacent", "学生社团招新摆摊要提前几天申请场地？", ["招新摆摊", "百团大战"]),
    ("na28", "adjacent", "校园马拉松比赛怎么报名？", ["马拉松"]),
    ("na29", "adjacent", "学校的心理咨询可以线上视频咨询吗？", ["视频咨询"]),
    ("na30", "adjacent", "转专业考试的历年真题在哪里能找到？", ["历年真题"]),
    ("na31", "adjacent", "教师职称评审答辩的PPT有模板吗？", ["答辩PPT", "PPT模板"]),
    ("na32", "adjacent", "研究生可以申请去企业挂职锻炼吗？", ["挂职锻炼"]),
    # external
    ("na33", "external", "北京市应届毕业生落户的积分标准是什么？", ["落户积分", "积分落户"]),
    ("na34", "external", "清华大学转专业需要什么条件？", ["清华大学"]),
    ("na35", "external", "今年考研新闻传播学的国家线是多少分？", ["国家线"]),
    ("na36", "external", "个税专项附加扣除里子女教育每月扣多少？", ["专项附加扣除"]),
    ("na37", "external", "北京住房公积金贷款最高能贷多少？", ["公积金贷款"]),
    ("na38", "external", "国家公务员考试应届生报名有年龄限制吗？", ["国考", "国家公务员考试"]),
    ("na39", "external", "出国留学的签证面签要准备什么材料？", ["面签"]),
    ("na40", "external", "北京地铁学生卡怎么办理？", ["地铁学生卡", "学生公交卡"]),
    ("na41", "external", "雅思考试一年有几次考试机会？", ["雅思"]),
    ("na42", "external", "中国人民大学的研究生宿舍是几人间？", ["中国人民大学", "人民大学"]),
    # instance
    ("na43", "instance", "上周五新闻学院学术讲座的课件能下载吗？", ["上周五"]),
    ("na44", "instance", "2026年11月的党委常委会决定了哪些事项？", ["2026年11月"]),
    ("na45", "instance", "这学期校运动会是几号开？", ["校运动会", "田径运动会"]),
    ("na46", "instance", "今年元旦晚会在哪个礼堂办？", ["元旦晚会"]),
    ("na47", "instance", "播音学院张老师的办公室在几号楼？", ["张老师"]),
    ("na48", "instance", "明天上午图书馆会停电吗？", ["停电通知"]),
    ("na49", "instance", "第35期中传校报头版讲的是什么？", ["第35期"]),
    # personal
    ("na50", "personal", "我的校园卡余额还剩多少？", ["余额查询结果"]),
    ("na51", "personal", "我上学期高数考了多少分？", ["高数"]),
    ("na52", "personal", "我的奖学金什么时候到账？", ["到账时间"]),
    ("na53", "personal", "我的报销单现在审批到哪一步了？", ["审批到哪"]),
    ("na54", "personal", "我的宿舍分到了哪栋楼？", ["分到了哪"]),
    # offtopic
    ("na55", "offtopic", "明天北京会下雨吗？", ["下雨"]),
    ("na56", "offtopic", "红烧肉怎么做才好吃？", ["红烧肉"]),
    ("na57", "offtopic", "Python里怎么把列表倒序？", ["Python", "列表倒序"]),
    ("na58", "offtopic", "比特币现在多少钱一个？", ["比特币"]),
    ("na59", "offtopic", "推荐几部好看的科幻电影", ["科幻电影"]),
    ("na60", "offtopic", "猫咪不吃饭是生病了吗？", ["猫咪"]),
    ("na61", "offtopic", "iPhone怎么截长图？", ["iPhone", "截长图"]),
    ("na62", "offtopic", "去云南旅游七天怎么安排路线？", ["云南旅游"]),
    # second round: replacements for drafts whose probes hit the corpus
    ("na63", "campus", "学校有没有能免费借雨伞的地方？", ["借雨伞", "共享雨伞"]),
    ("na64", "campus", "校园里哪里能买到鲜花？", ["鲜花店", "花店"]),
    ("na65", "campus", "学校的乒乓球台需要自己带球拍吗？", ["乒乓球台", "球拍"]),
    ("na66", "campus", "学校附近有没有便宜的打印复印店推荐？", ["复印店"]),
    ("na67", "campus", "宿舍楼有没有公共微波炉可以热饭？", ["微波炉"]),
    ("na68", "adjacent", "研究生毕业论文查重可以免费查几次？", ["免费查重", "查重次数"]),
    ("na69", "adjacent", "教职工的年度体检能带家属一起去吗？", ["带家属体检", "家属体检"]),
    ("na70", "adjacent", "本科生可以申请去校外实习抵学分吗，能抵几个学分？", ["实习抵学分", "抵学分"]),
    ("na71", "adjacent", "校医院可以开具新冠疫苗接种证明吗？", ["新冠疫苗", "接种证明"]),
    ("na72", "adjacent", "毕业生离校时校园卡里的余额怎么退？", ["余额退还", "退余额"]),
    ("na73", "external", "北京大学的博士生每月补助是多少？", ["北京大学博士"]),
    ("na74", "external", "教育部今年的“强基计划”招哪些专业？", ["强基计划"]),
    ("na75", "external", "托福考试成绩有效期是几年？", ["托福"]),
    ("na76", "instance", "这周三下午的全校教职工大会在哪开？", ["教职工大会"]),
    ("na77", "instance", "今年春季学期的期末考试安排表出来了吗？", ["期末考试安排表"]),
    ("na78", "personal", "我的四级成绩什么时候能查？", ["四级成绩"]),
    ("na79", "offtopic", "怎么提高手机电池续航？", ["电池续航"]),
    ("na80", "offtopic", "世界杯下一届在哪个国家举办？", ["世界杯"]),
]


# Manual review of the Top1 chunk for every case scoring >= 0.55 with qwen3-rerank
# (eval/ab/noanswer_eval.py). "answerable" cases are dropped; "partial" cases go to a
# separate list: the KB has general information an assistant should use, so they are
# neither a clean refusal nor a clean answer and stay out of threshold calibration.
REVIEW = {
    "na40": ("answerable", "学生电子交通卡申领：电子学生卡 / 学生交通联名卡办理流程"),
    "na70": ("answerable", "本科生院 FAQ：自主参加的校外实习不能认定实践学分"),
    "na09": ("answerable", "场馆.docx：体育场馆（个人预约）系统入口与规则"),
    "na50": ("partial", "校园卡在中传 APP/公众号界面查余额，个人余额无法回答"),
    "na52": ("partial", "资助资金管理办法：奖学金于每年 12 月 31 日前发放"),
    "na54": ("partial", "学生公寓服务中心：各楼对应的学生类别，个人分配无法回答"),
    "na43": ("partial", "图书馆手册：讲座及培训课件挂在图书馆页面下载"),
    "na26": ("partial", "实验室开放预约走实验室综合管理系统，未提 3D 打印机"),
    "na77": ("partial", "本科生院 FAQ：期末考试安排通常在第 17 周前公布"),
    "na68": ("partial", "图书馆提供免费查重，次数未写"),
    "na11": ("partial", "公共教室：延时自习室开放到 22:00，无通宵"),
    "na22": ("partial", "迎新：汉语进修班报到与分班考试安排"),
    "na16": ("partial", "人事：高校教师资格认定需参加学校岗前培训"),
}


def norm(text):
    return re.sub(r"\s+", "", text or "")


def main():
    chunks_path = ROOT / "data" / "chunks.jsonl"
    chunks = [json.loads(line) for line in chunks_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    hay = [norm(c["text"]) + "\n" + norm(c.get("doc_title", "")) for c in chunks if c.get("status") == "active"]

    existing = set()
    for path in (ROOT / "eval/heldout/dataset_heldout_v1.json", ROOT / "eval/dataset_v5_personas.json"):
        existing |= {c["query"] for c in json.loads(path.read_text(encoding="utf-8"))["no_answer"]}

    cases, partial, dropped = [], [], 0
    for case_id, category, query, probes in CASES:
        hits = {p: sum(1 for h in hay if norm(p) in h) for p in probes}
        found = {p: n for p, n in hits.items() if n}
        if found:
            print("PROBE FOUND", case_id, query, found)
            dropped += 1
            continue
        if query in existing:
            print("DUPLICATE", case_id, query)
            dropped += 1
            continue
        case = {"id": case_id, "category": category, "query": query, "probes": probes}
        verdict, reason = REVIEW.get(case_id, (None, None))
        if verdict == "answerable":
            print("ANSWERABLE", case_id, query, "->", reason)
            dropped += 1
        elif verdict == "partial":
            partial.append({**case, "review": reason})
        else:
            cases.append(case)

    dataset = {
        "meta": {
            "name": "CUC KB no-answer v1",
            "created": "2026-11-01",
            "chunks_md5_source": "data/chunks.jsonl (production 2026-11-01)",
            "note": "每题探测词在 active 语料（正文+标题）中零命中；qwen3-rerank 得分 >=0.55 的题逐条人工阅读 Top1：可答的剔除，部分可答的放入 partial，不参与阈值标定",
        },
        "no_answer": cases,
        "partial": partial,
    }
    out = Path(__file__).with_name("dataset_noanswer_v1.json")
    out.write_text(json.dumps(dataset, ensure_ascii=False, indent=1), encoding="utf-8")
    by_cat = {}
    for c in cases:
        by_cat[c["category"]] = by_cat.get(c["category"], 0) + 1
    print(f"no_answer={len(cases)} partial={len(partial)} dropped={dropped} by_category={by_cat} -> {out}")


if __name__ == "__main__":
    main()
