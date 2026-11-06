import type { TFunction } from 'i18next'

/** Translate product metadata while preserving source content and identifiers. */
export function studioLabel(t: TFunction, value?: string | null): string {
  const labels: Record<string, string> = {
    activity: t('原文练习'), evidence: t('原文证据'), observation: t('观测记录'),
    claim: t('研究主张'), run: t('实验记录'), proposal: t('稿件修改'),
    artifact: t('研究图表'), remote_snapshot: t('读取快照'), edition: t('文献版本'),
    manuscript: t('稿件'), work: t('资料'), quote: t('文字摘录'), figure: t('图'),
    table: t('表格'), equation: t('公式'), text: t('文本'),
    reported: t('论文报告'), executed: t('实际运行'), static_check: t('静态检查'),
    hypothesis: t('研究假设'), static_only: t('仅静态检查'),
    planned: t('待运行 / 核对'), running: t('运行中'), succeeded: t('运行成功'),
    failed: t('运行失败'), cancelled: t('已取消'), interrupted: t('已中断'),
    finished: t('已结束'), success: t('运行成功'), FINISHED: t('已结束'),
    RUNNING: t('运行中'), FAILED: t('运行失败'), KILLED: t('已终止'),
    committed: t('已保存'), prepared: t('待完成'), draft: t('待核实'),
    confirmed: t('已核对'), disputed: t('存在反证'), pending: t('待审阅'),
    accepted: t('已接受'), rejected: t('已拒绝'), complete: t('已完成'),
    unknown: t('未知'), not_reviewed: t('待核对'), verified: t('已核对'),
    current: t('已同步'), ready: t('待同步'), paused: t('已断开'),
    retrying: t('等待重试'), unauthorized: t('需要重新授权'),
    manifest: t('运行清单'), environment: t('环境'), configuration: t('配置'),
    metrics: t('指标'), log: t('原始日志'), documentation: t('说明文档'),
    profile: t('任务口径'), direction: t('指标方向'), execution_witness: t('执行见证'),
    general: t('通用定量比较'), hoi_detection: t('HOI 交互检测'),
    hoi_reconstruction: t('HOI 三维重建'), trajectory: t('轨迹评估'),
    mathematics: t('数学与数值方法'), higher: t('越高越好'), lower: t('越低越好'),
    target: t('重建对象'), template_dependency: t('模板依赖'),
    calibration: t('标定条件'), coordinate_space: t('坐标空间'),
    trajectory_source: t('轨迹来源'), scale_normalization: t('尺度归一化'),
    evaluation_version: t('评估版本'), assumptions: t('前提假设'), domain: t('适用范围'),
    numbers_units: t('数值与单位'), citations: t('文献引用'),
    equations: t('公式'), causality: t('因果表述'), comparison: t('比较结论'),
    negation: t('否定表述'), scope: t('适用边界'),
    section: t('章节'), subsection: t('小节'), subsubsection: t('子小节'),
    chapter: t('章'), part: t('部分'), paragraph: t('段落'), label: t('标签'),
  }
  return value ? labels[value] || t(value) : ''
}
