# 目标会议与期刊（CCF 第七版 A 类）

来源：CCF 推荐国际学术会议和期刊目录（第七版，2026-03-31 发布），核对日期 2026-10-31。机器可读版本在 `assets/venues.json`，`render.py --format form` 据此生成会议表单。

## 表单状态

- `verified: true`：已按当年官方审稿指南核对（目前：ICML 2026）。
- `verified: false`：近年常见形式或通用模板。生成的表单会提示“请核对当年审稿表单”。使用前若能联网，打开 `guide_url` 核对字段与量表；如有变化，更新 `venues.json` 的 `forms`。

## 选择规则

1. 用户指定的会议/期刊 → 用对应 `form`；
2. 没有专属表单的会议 → `generic_conference`（1–6 分）；期刊 → `generic_journal`（拒稿/重投/大修/小修/接收）；
3. 期刊审稿强调完整性和扩展性；若是会议论文扩展版，检查新增内容（见 paper-types.md）。

## A 类全表（括号内为使用的表单）

**人工智能**  
- 会议：AAAI（通用）、NeurIPS（NeurIPS）、ACL（ACL）、CVPR（CVPR）、ICCV（ICCV）、ICML（ICML）、ICLR（ICLR）
- 期刊：AI（通用）、TPAMI（通用）、IJCV（通用）、JMLR（通用）

**计算机图形学与多媒体**  
- 会议：ACM MM（通用）、SIGGRAPH（通用）、VR（通用）、IEEE VIS（通用）
- 期刊：TOG（通用）、TIP（通用）、TVCG（通用）、TMM（通用）

**数据库/数据挖掘/内容检索**  
- 会议：SIGMOD（通用）、SIGKDD（通用）、ICDE（通用）、SIGIR（通用）、VLDB（通用）
- 期刊：TODS（通用）、TOIS（通用）、TKDE（通用）、VLDBJ（通用）

**人机交互与普适计算**  
- 会议：CSCW（通用）、CHI（通用）、UbiComp（通用）、UIST（通用）
- 期刊：TOCHI（通用）、IJHCS（通用）

**交叉/综合/新兴**  
- 会议：WWW（通用）、RTSS（通用）
- 期刊：JACM（通用）、Proc. IEEE（通用）、SCIS（通用）、Bioinformatics（通用）

**计算机体系结构/并行与分布计算/存储系统**  
- 会议：PPoPP（通用）、FAST（通用）、DAC（通用）、HPCA（通用）、MICRO（通用）、SC（通用）、ASPLOS（通用）、ISCA（通用）、ACM SIGOPS ATC（通用）、EuroSys（通用）、HPDC（通用）
- 期刊：TOCS（通用）、TOS（通用）、TCAD（通用）、TC（通用）、TPDS（通用）、TACO（通用）

**计算机网络**  
- 会议：SIGCOMM（通用）、MobiCom（通用）、INFOCOM（通用）、NSDI（通用）
- 期刊：JSAC（通用）、TMC（通用）、TON（通用）

**网络与信息安全**  
- 会议：CCS（通用）、EUROCRYPT（通用）、S&P（通用）、CRYPTO（通用）、USENIX Security（通用）、NDSS（通用）
- 期刊：TDSC（通用）、TIFS（通用）、JoC（通用）

**软件工程/系统软件/程序设计语言**  
- 会议：PLDI（通用）、POPL（通用）、FSE（通用）、SOSP（通用）、OOPSLA（通用）、ASE（通用）、ICSE（通用）、ISSTA（通用）、OSDI（通用）、FM（通用）
- 期刊：TOPLAS（通用）、TOSEM（通用）、TSE（通用）、TSC（通用）

**计算机科学理论**  
- 会议：STOC（通用）、SODA（通用）、CAV（通用）、FOCS（通用）、LICS（通用）
- 期刊：TIT（通用）、IANDC（通用）、SICOMP（通用）

## 相关的非 A 类（第七版）

ECCV、IJCAI（第七版由 A 降为 B）、ICRA、EMNLP、NAACL、COLT、UAI、TNNLS、T-RO 为 B 类；AISTATS、IROS 为 C 类；CoRL、RSS 未收录。均使用通用表单。
