#!/usr/bin/env python3
"""Build held-out v2 (eval/heldout/dataset_heldout_v2.json) for the 2026-11-07
official-site corpus (cuc_kb_v3, 2247 docs).

Rules (same discipline as held-out v1):
- Every curated question targets a document added in the 2026-11-07 batch
  (present in data-v3 but not in the old 311-doc data/) and not referenced by
  any earlier dataset (v1-v5, heldout v1) — checked via inventory_v3.py logic.
- Questions are colloquial paraphrases; answer keywords are verified verbatim
  (whitespace-normalized) in the expected document's active chunks.
- Citation cases derive expected_page from the chunk that carries the keyword.
- This set is NOT used for tuning; it is a fresh holdout for the new corpus.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# (id, query, expected doc title substrings, answer keywords)
CASES = [
    ("v01", "批量买低值易耗品，多少台件以上要按固定资产管理？", ["低值易耗品管理办法"], ["批量50台件以上"]),
    ("v02", "单位给职工配笔记本电脑，数量上限按实有人数的多少算？", ["通用办公设备及家具配置标准"], ["便携式计算机配置数量上限为单位编制内实有人数的50%"]),
    ("v03", "自己保管的相机弄丢了，要按设备原值的多少赔偿？", ["仪器设备损坏、丢失赔偿实施细则"], ["设备原值50%—100%的赔款"]),
    ("v04", "学生戴的校徽胸徽是什么配色？", ["校名、校徽使用管理办法"], ["学生佩戴的胸徽为黄框、白底、红字"]),
    ("v05", "学校周转房最长可以租住几年？", ["周转房屋管理办法"], ["租住期最长为三年"]),
    ("v06", "科研用的单台套设备价值多少万以上必须进国家网络管理平台？", ["大型仪器设备开放共享管理办法"], ["单台套价值50万元（含）以上的科研用途仪器设备"]),
    ("v07", "公租房合同到期想续租，要提前多久申请？", ["公共租赁住房申请和配租指南"], ["合同期满前3个月"]),
    ("v08", "实验室里化学试剂溅进眼睛，至少要冲洗多长时间？", ["实验室安全事故应急预案"], ["时间应不少于15分钟"]),
    ("v09", "学校的互联网域名归哪个部门管？", ["无形资产管理办法（修订）"], ["信息化处负责学校互联网域名"]),
    ("v10", "两百万以上的实验室建设项目验收，至少要请几名校外专家？", ["实验室建设项目验收工作指南"], ["至少2名校外"]),
    ("v11", "海南国际学院借摄像器材有问题找哪位老师？", ["海南国际学院仪器设备借用操作指南"], ["19903917681"]),
    ("v12", "学校绩点算法里考60分对应绩点是多少？", ["百分制分数转换绩点"], ["60分绩点为1"]),
    ("v13", "英语四六级下半年一般什么时候报名？", ["关于参加全国大学英语等级考试的规定"], ["九月底或十月初"]),
    ("v14", "2026级本科新生哪天开始上课？", ["2026—2027学年校历"], ["本科新生上课：10月8日"]),
    ("v15", "现在转专业一次能填几个志愿？", ["本科生转专业管理办法（2025年修订）"], ["每人可填报两个目标专业志愿"]),
    ("v16", "缺课超过总学时多少会被取消期末考试资格？", ["全日制本科生课程考核及成绩管理办法"], ["累计缺课（含请假）超过课程总学时1/3"]),
    ("v17", "申请缓考原则上要提前多久在教务系统里发起？", ["全日制本科生课程考核及成绩管理办法"], ["至少提前24小时在教务管理系统发起"]),
    ("v18", "退役大学生士兵申请体测免测通过后按多少分记？", ["〔2024〕115号"], ["申请通过的当年测试成绩以95分计入"]),
    ("v19", "少数民族预科生休学能办几次？", ["少数民族预科生管理办法（修订）"], ["预科阶段休学只能办理一次"]),
    ("v20", "体育课必选项目不及格最多能重考几次？", ["关于印发《中国传媒大学本科生体育课程管理规定》的通知"], ["必选项目最多可重考四次"]),
    ("v21", "辅修双学位平均绩点要达到多少才能拿学位证？", ["辅修_双学位管理办法"], ["平均学分绩点不低于2.0"]),
    ("v22", "往届生自助办中英文成绩单，第二份起收多少钱？", ["往届生自助开成绩单指南"], ["第二份起10元/份"]),
    ("v23", "补办毕业证明书交的照片要压缩到多大？", ["毕业证明书_学位证明书办理流程"], ["照片大小压缩在30K之内"]),
    ("v24", "辅修课程补考一般安排在什么时候？", ["辅修双学位常见问题"], ["一般安排在5月初"]),
    ("v25", "团体参观传媒博物馆要提前几个工作日预约？", ["传媒博物馆团体参观预约办法"], ["提前至少2个工作日进行线上预约"]),
    ("v26", "校内单位想用传媒博物馆临展展厅，要提前多久申请？", ["传媒博物馆临时展厅管理办法"], ["至少提前30天向传媒博物馆提交场地使用申请"]),
    ("v27", "新生户口迁移证最晚什么时候交？", ["集体户口管理暂行规定"], ["截止时间为入学当年的10月15日"]),
    ("v28", "毕业论文用了AI但没交使用情况说明会怎样？", ["人工智能使用规范实施细则"], ["未提交人工智能使用情况说明者，不得参加答辩"]),
    ("v29", "硕士论文查重总文字复制比要低于多少才算合格？", ["研究生学位论文查重检测的暂行规定"], ["硕士学位论文查重检测总文字复制比应低于20%"]),
    ("v30", "博士入学取得学籍满多久可以申请转成硕士培养？", ["硕博连读生、直博生转为硕士生培养管理办法"], ["取得博士生学籍满一年后"]),
    ("v31", "博士论文匿名评阅最多可以申请几位专家回避？", ["博士学位论文匿名评阅办法"], ["需回避的专家最多为三名"]),
    ("v32", "图书馆弄丢一本1975年出版的书要赔原价的多少倍？", ["图书馆文献借阅规定"], ["按原书价的50倍赔偿"]),
    ("v33", "得到读书奖学金每年奖多少人、每人多少？", ["得到读书奖学金"], ["奖励人数40人，奖励标准为2500元/人"]),
    ("v34", "毕业生暂缓发档的档案最晚能在学校保留到什么时候？", ["2026届毕业生档案转寄信息填写须知"], ["最晚保留至2028年6月15日"]),
    ("v35", "国际会议的工作人员数量要控制在正式代表的多少以内？", ["会议费管理办法（修订）"], ["国际会议工作人员人数控制在会议正式代表人数的10％以内"]),
    ("v36", "教职工借款原则上最长多久要冲账？", ["《中国传媒大学往来款项管理办法（试行）》（中传财字〔2022〕135号）"], ["所有借款业务冲账期限不超过三个月"]),
    ("v37", "批量买东西超过几件发票就要附销售清单？", ["关于税务发票报销的说明"], ["单次购买在8件（不含）以上"]),
    ("v38", "校外人员劳务费必须用哪家银行的卡发？", ["财务综合服务平台常见问题解答"], ["校外人员劳务发放必须使用工商银行卡号"]),
    ("v39", "项目聘用人员参与本单位科研项目多久以上才能申办公务卡？", ["项目聘用人员申办公务卡情况说明"], ["参与本单位科研项目一年以上"]),
    ("v40", "海南国际学院新生入学发的是哪家银行的卡？", ["2024-2025学年学生缴费通知"], ["为每位新生配备一张招商银行借记卡"]),
    ("v41", "基金会公益科研项目餐费人均不能超过多少？", ["传媒教基字〔2026〕4号"], ["人均标准不超过80元"]),
    ("v42", "科研项目档案要在项目完成后多久内归档？", ["档案问答"], ["科研类档案应当在项目完成后两个月内归档"]),
    ("v43", "暑假办综合档案业务打哪个电话？", ["关于2026年暑假期间学校综合档案业务办理的通知"], ["010-65779232"]),
    ("v44", "给校史馆捐赠史料后，捐赠人对捐赠品享有什么权利？", ["校史馆接受校史资料（实物）捐赠工作管理办法"], ["甲方对其捐赠的史料与实物享有无偿利用的权利"]),
    ("v45", "校史馆收藏的那面天安门国旗编号是多少？", ["校史馆馆藏国旗教育活动管理办法"], ["编号2024-0270"]),
    ("v46", "纸质档案库房的温度要控制在什么范围？", ["档案馆库房管理制度"], ["环境温度应控制在14-24℃"]),
    ("v47", "学校每年哪几个月集中办理退宿？", ["学生公寓退宿管理规定"], ["原则上每年1月、6月、9月学校集中受理"]),
    ("v48", "望京方向的班车早上几点发车？", ["班车路线通告"], ["每天早上7：10分发车"]),
    ("v49", "校内挂横幅最长能挂几天？", ["校内悬挂横幅标语、设置宣传广告管理办法"], ["悬挂和设置期限最长为7天"]),
    ("v50", "上课迟到超过多少分钟算重大教学事故？", ["教学事故认定和处理办法"], ["时长超过20分钟"]),
    ("v51", "督导专家常规随堂听课津贴一课时多少钱？", ["教学督导工作细则"], ["常规督导听课、思政课日常督导听课津贴标准为150元/课时"]),
    ("v52", "课堂有我随堂评价的有效期是多久？", ["“课堂有我”系统操作手册（学生版）"], ["有效期为72小时"]),
    ("v53", "没有工资收入的学生党员每月交多少党费？", ["党费收缴、管理和使用办法"], ["每月交纳党费0.2元"]),
    ("v54", "无正当理由连续多久不交党费按自行脱党处理？", ["党费收缴、管理和使用办法"], ["连续6个月不交纳党费的党员，按自行脱党处理"]),
    ("v55", "党员组织关系介绍信有效期一般最长多久？", ["接转组织关系知识问答"], ["一般不应超过3个月"]),
    ("v56", "学校按每名党员每年多少钱标准预算党建活动经费？", ["基层党组织党建活动经费管理办法"], ["每名党员每年300元标准"]),
    ("v57", "每个学生每学年最多能参加几个社团？", ["学生社团建设管理办法"], ["每名学生每学年最多参与2个学生社团"]),
    ("v58", "中传青年五四奖章参评青年的年龄范围是多少？", ["中传青年五四奖章"], ["年龄在14岁至35岁之间"]),
    ("v59", "非学历教育项目收入要上缴学校百分之多少？", ["非学历教育管理规定"], ["非学历教育项目总收入的20%上缴学校"]),
    ("v60", "继续教育7月批次注册的毕业证一般几月发？", ["高等学历继续教育毕业审核流程和毕业证书发放时间安排的说明"], ["毕业证一般于4月、10月发放"]),
    ("v61", "2026年普通话水平测试培训班第五期培训费多少？", ["普通话水平测试培训班（第五期）"], ["每人4980元"]),
    ("v62", "信通学院AI实验班三年必修限选课平均绩点不能低于多少？", ["信通学院政〔2026〕5号"], ["三学年必修限选课平均学分绩点不低于3.3"]),
    ("v63", "信通学院科研实验室开放到晚上几点？", ["信息与通信工程学院科研实验室安全管理办法"], ["实验室开放时间为上午8:00到晚上21:30"]),
    ("v64", "考试开始多久之后允许交卷出场？", ["考场规则及监考人员守则（2026）"], ["考试30分钟后，允许交卷出场"]),
    ("v65", "学校的大模型服务现在收费吗？", ["大模型服务管理实施细则"], ["大模型服务目前免费提供"]),
    ("v66", "校园网每月给多少免费基础流量？", ["校园网管理办法（试行）"], ["50GB的免费基础流量"]),
    ("v67", "学校正版Adobe可以在几台电脑上激活？", ["Adobe正版化服务使用指南"], ["可在两台计算机上登录（激活）"]),
    ("v68", "校园统一支付平台学生账号的初始密码是什么？", ["新生缴费指南"], ["初始密码为身份证后六位"]),
    ("v69", "三年制硕士申请提前毕业要在什么时候提出？", ["三年制硕士研究生提前毕业的规定"], ["申请人于第3学期末提出申请"]),
    ("v70", "博士生基本文献阅读至少要多少种？", ["博士候选人资格考试管理规定"], ["基本文献阅读数量不少于150种"]),
    ("v71", "学硕基本文献考试前要读多少种文献？", ["学术学位硕士研究生基本文献考试管理规定"], ["基本文献阅读数量不少于80种"]),
    ("v72", "已毕业学生想在线查本人录取名册用哪个平台？", ["关于2026年寒假期间学校综合档案业务办理的通知"], ["daly.cuc.edu.cn"]),
    ("v73", "教授连续两年不给本科生上课会怎样？", ["教授、副教授为本科生上课的规定"], ["连续两年不讲授本科课程的，不再聘任其担任现任职务"]),
    ("v74", "优秀教学奖每次评多少名？", ["优秀教学奖评选及奖励办法"], ["每次15名"]),
    ("v75", "学术伦理委员会多久开展一次伦理审查？", ["伦理审查流程及申请表"], ["每月最后一周学术伦理委员会开展审查"]),
    ("v76", "自行委托专利代理后，受理通知书要在多少个工作日内交科技处？", ["发明人自行委托专利代理机构申请专利流程"], ["在15个工作日到30个工作日之间"]),
    ("v77", "教职工停车年度申报有问题打哪个咨询电话？", ["停车年度申报审核"], ["65783111"]),
    ("v78", "集体政审用的无犯罪证明要在哪个APP上开？", ["10人以上集体政审说明"], ["公安一网通办"]),
    ("v79", "全国性学科竞赛一等奖能认几个创新学分？", ["本科生创新创业学分认定管理办法"], ["全国性竞赛、国际性竞赛一等奖可获得5个创新学分"]),
    ("v80", "出国交流超过一学期要办什么手续？", ["本科学生境外交流学习管理规定（修订）"], ["需办理保留学籍手续"]),
    ("v81", "外来务工人员进校几天内要办临时出入证？", ["外来务工经商人员管理规定"], ["进校五日内，须到保卫处办理"]),
    ("v82", "办大型文体活动要提前几天填登记审批表？", ["大型文体活动安全管理规定"], ["应在活动举办的三日前填写"]),
    ("v83", "国际加速专线申请由哪个部门审批？", ["国际加速专线服务管理实施细则"], ["党委宣传部审批"]),
    ("v84", "少数民族预科生第二学期最多选修几学分的本科通识拓展课？", ["少数民族预科生管理办法（修订）"], ["不多于2学分的本科层次通识教育拓展课"]),
]

SHORT = [
    ("s01", "退宿", ["学生公寓退宿管理规定"], ["退宿"]),
    ("s02", "班车", ["班车路线通告"], ["班车"]),
    ("s03", "党费", ["党费收缴、管理和使用办法", "交纳党费知识问答"], ["党费"]),
    ("s04", "论文查重", ["研究生学位论文查重检测的暂行规定"], ["复制比"]),
    ("s05", "缓考", ["全日制本科生课程考核及成绩管理办法", "关于学生因病办理免修、缓考等管理规定"], ["缓考"]),
    ("s06", "校徽", ["校名、校徽使用管理办法"], ["校徽"]),
    ("s07", "周转房", ["周转房屋管理办法"], ["周转房"]),
    ("s08", "辅修", ["辅修_双学位管理办法", "辅修双学位常见问题"], ["辅修"]),
    ("s09", "学分绩点", ["百分制分数转换绩点", "全日制本科生课程考核及成绩管理办法"], ["绩点"]),
    ("s10", "户口迁移", ["集体户口管理暂行规定"], ["户口迁移"]),
    ("s11", "教学事故", ["教学事故认定和处理办法"], ["教学事故"]),
    ("s12", "社团注册", ["学生社团建设管理办法"], ["社团"]),
    ("s13", "普通话培训", ["普通话水平测试培训班"], ["普通话"]),
    ("s14", "查档案", ["档案问答", "关于2026年暑假期间学校综合档案业务办理的通知"], ["档案"]),
    ("s15", "公务卡", ["项目聘用人员申办公务卡情况说明"], ["公务卡"]),
    ("s16", "实验室开放时间", ["信息与通信工程学院科研实验室安全管理办法"], ["开放时间"]),
]

# (id, assistant, query, expected docs)
ISOLATION = [
    ("i01", "财务处小助手", "借款原则上多久必须冲账？", ["往来款项管理办法"]),
    ("i02", "档小管", "科研类档案项目完成后多久归档？", ["档案问答"]),
    ("i03", "中传小资", "多少万以上的科研设备要进国家网络平台？", ["大型仪器设备开放共享管理办法"]),
    ("i04", "中传教务君", "转专业能填几个志愿？", ["本科生转专业管理办法（2025年修订）"]),
    ("i05", "新生帮手", "博士论文匿名评阅能申请几位专家回避？", ["博士学位论文匿名评阅办法"]),
    ("i06", "党建知识助手", "学生党员每月交多少党费？", ["党费收缴、管理和使用办法"]),
    ("i07", "团委小助手", "一学年最多参加几个社团？", ["学生社团建设管理办法"]),
    ("i08", "中传培训小帮手", "非学历教育收入上缴学校多少？", ["非学历教育管理规定"]),
    ("i09", "督导处小助手", "常规督导听课津贴多少一课时？", ["教学督导工作细则"]),
    ("i10", "中传小勤", "学校哪几个月集中办退宿？", ["学生公寓退宿管理规定"]),
]

# (id, query, expected docs, keyword used to locate the cited page)
CITATION = [
    ("c01", "笔记本电脑配置数量上限是多少？", ["通用办公设备及家具配置标准"], ["便携式计算机配置数量上限为单位编制内实有人数的50%"]),
    ("c02", "化学试剂溅入眼睛要冲洗多久？", ["实验室安全事故应急预案"], ["时间应不少于15分钟"]),
    ("c03", "大型仪器验收后多久要纳入共享平台？", ["大型仪器设备开放共享管理办法"], ["完成验收之日起30个工作日内"]),
    ("c04", "学生党员每月交多少党费？", ["党费收缴、管理和使用办法"], ["每月交纳党费0.2元"]),
    ("c05", "每名学生每学年最多参与几个社团？", ["学生社团建设管理办法"], ["每名学生每学年最多参与2个学生社团"]),
    ("c06", "国际会议工作人员比例限制是多少？", ["会议费管理办法（修订）"], ["国际会议工作人员人数控制在会议正式代表人数的10％以内"]),
    ("c07", "科研档案和基建档案分别多久归档？", ["档案问答"], ["科研类档案应当在项目完成后两个月内归档"]),
    ("c08", "公益科研项目餐费人均标准是多少？", ["传媒教基字〔2026〕4号"], ["人均标准不超过80元"]),
    ("c09", "毕业生在线查档平台的网址是什么？", ["关于2026年寒假期间学校综合档案业务办理的通知"], ["daly.cuc.edu.cn"]),
    ("c10", "校园网免费基础流量是多少？", ["校园网管理办法（试行）"], ["50GB的免费基础流量"]),
]

DATASETS = [
    "eval/dataset.json", "eval/dataset_v2.json", "eval/dataset_v3.json",
    "eval/dataset_v4_noassistant.json", "eval/dataset_v5_personas.json",
    "eval/heldout/dataset_heldout_v1.json",
]


def norm(text):
    return re.sub(r"\s+", "", text or "")


def main():
    old = {d["doc_id"] for d in json.loads((ROOT / "data/docs.json").read_text(encoding="utf-8"))}
    docs = {d["doc_id"]: d for d in json.loads((ROOT / "data-v3/docs.json").read_text(encoding="utf-8"))}
    added = {doc_id for doc_id in docs if doc_id not in old}
    by_doc = {}
    with (ROOT / "data-v3/chunks.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            c = json.loads(line)
            if c.get("status") == "active":
                by_doc.setdefault(c["doc_id"], []).append(c)

    referenced = set()
    for rel in DATASETS:
        data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        for section in ("curated", "short", "isolation", "citation"):
            for case in data.get(section, []):
                referenced.update(case.get("expected_docs", []))

    problems = 0

    def resolve(expected, keywords, case_id, need_new=True):
        nonlocal problems
        hit_docs = [d for d, cs in by_doc.items()
                    if any(e in cs[0]["doc_title"] for e in expected)
                    and any(norm(k) in norm(c["text"]) for c in cs for k in keywords)]
        if not hit_docs:
            print("MISS", case_id, expected, keywords)
            problems += 1
            return None
        if need_new and not (set(hit_docs) & added):
            print("NOT-NEW", case_id, [docs[d]["title"] for d in hit_docs])
            problems += 1
        if any(e in docs[d]["title"] for d in hit_docs for e in referenced):
            # warning only: same-text old copy may share the title; the query
            # itself was verified unused by earlier sets (see meta note)
            print("TITLE-SHARED-WITH-OLD-SET", case_id, [docs[d]["title"] for d in hit_docs])
        others = sorted({docs[d]["title"][:36] for d, cs in by_doc.items()
                         if d in docs and d not in hit_docs
                         and any(norm(k) in norm(c["text"]) for c in cs for k in keywords)})
        if others:
            print("KW-ALSO-IN", case_id, "->", others[:5])
        return hit_docs

    curated = []
    for case_id, query, expected, keywords in CASES:
        hit_docs = resolve(expected, keywords, case_id)
        if hit_docs is None:
            continue
        meta = docs[hit_docs[0]]
        curated.append({
            "id": case_id, "dept": meta.get("department", ""), "query": query,
            "expected_docs": expected, "keywords": keywords,
            "intended_assistant": meta["assistants"][0],
        })

    short = []
    for case_id, query, expected, keywords in SHORT:
        hit_docs = resolve(expected, keywords, case_id, need_new=False)
        if hit_docs is None:
            continue
        short.append({"id": case_id, "dept": docs[hit_docs[0]].get("department", ""),
                      "query": query, "expected_docs": expected, "keywords": keywords})

    isolation = []
    for case_id, assistant, query, expected in ISOLATION:
        docs_match = [d for d, cs in by_doc.items() if any(e in cs[0]["doc_title"] for e in expected)]
        if not docs_match:
            print("ISO-DOC-MISSING", case_id, expected)
            problems += 1
            continue
        if not any(assistant in docs[d]["assistants"] or "shared" in docs[d]["assistants"] for d in docs_match):
            print("ISO-ASSISTANT-MISMATCH", case_id, assistant, [docs[d]["assistants"] for d in docs_match])
            problems += 1
            continue
        isolation.append({"id": case_id, "assistant": assistant, "query": query, "expected_docs": expected})

    citation = []
    for case_id, query, expected, keywords in CITATION:
        hit_docs = resolve(expected, keywords, case_id)
        if hit_docs is None:
            continue
        page = None
        for d in hit_docs:
            for c in sorted(by_doc[d], key=lambda x: x["chunk_id"]):
                if any(norm(k) in norm(c["text"]) for k in keywords):
                    page = int(c["page_start"])
                    break
            if page:
                break
        citation.append({"id": case_id, "query": query, "expected_docs": expected,
                         "expected_page": page, "page_phrase": keywords[0]})

    dataset = {
        "meta": {
            "name": "CUC KB held-out v2 (official-site corpus)",
            "created": "2026-11-07",
            "corpus": "data-v3 (cuc_kb_v3, 2247 docs / 49571 chunks)",
            "note": "事实题针对 2026-11-07 官网批次新增文档、问点均未被 v1-v5/heldout-v1 使用；口语化改写；关键词逐条在 active chunk 原文核验；个别规章与旧语料同名同文（官网重复采集，如创新创业学分/境外交流），期望命中任一副本均计对；引用题页码取自含关键词 chunk 的 page_start；未参与任何调参",
        },
        "curated": curated, "short": short, "no_answer": [],
        "isolation": isolation, "citation": citation,
    }
    out = Path(__file__).with_name("dataset_heldout_v2.json")
    out.write_text(json.dumps(dataset, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"curated={len(curated)} short={len(short)} isolation={len(isolation)} citation={len(citation)} problems={problems} -> {out}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())