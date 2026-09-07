import { BellRing, Gauge, Settings } from "lucide-react";
import { useOutletContext } from "react-router-dom";

import type { ConsoleContextValue } from "../context";
import { PageHeader } from "../Primitives";

type CapabilityKind = "performance" | "alerts" | "settings";

const CONTENT: Record<CapabilityKind, { title: string; eyebrow: string; icon: React.ReactNode; summary: string; blockers: string[]; next: string[] }> = {
  performance: { title: "性能分析", eyebrow: "Performance / 未提供标准聚合", icon: <Gauge />, summary: "当前 Collector 可以保留性能类原始事件，但服务端尚未提供可解释的启动、卡顿、网络和页面性能聚合 API。", blockers: ["客户端需冻结标准指标名、单位、采样与 occurrence identity", "需要 ClickHouse/SigNoz 或预聚合层承载高基数时序查询", "需要定义分位数、基线和弱网/设备维度的覆盖状态"], next: ["启动与页面耗时 P50/P75/P95", "卡顿/Jank 与慢帧归因", "网络失败率、DNS/TLS/TTFB 分解"] },
  alerts: { title: "告警", eyebrow: "Alerting / UNCONFIGURED", icon: <BellRing />, summary: "仓库包含 SigNoz 告警模板，但当前控制台没有生产告警规则、通知渠道、抑制、去重和送达状态 API。", blockers: ["需要部署并验证固定版本 SigNoz", "需要配置通知渠道与凭据托管", "需要告警生命周期、静默和租户权限模型"], next: ["Crash/ANR 阈值与发布窗口规则", "告警实例、确认、恢复和静默", "Webhook/邮件/IM 送达审计"] },
  settings: { title: "设置", eyebrow: "Scope / 只读现状", icon: <Settings />, summary: "tenant/app/environment 当前由 apmq1 凭据固定；Web 暂无应用管理、成员、密钥轮换和保留策略写接口。", blockers: ["需要管理员身份与独立管理面", "需要凭据生命周期、RBAC 和审计 UI", "需要存储保留、删除和合规策略"], next: ["应用与环境管理", "成员角色和 Query Key 轮换", "采样、保留与隐私策略"] },
};

export function CapabilityPage({ kind }: { kind: CapabilityKind }) {
  const { session } = useOutletContext<ConsoleContextValue>();
  const content = CONTENT[kind];
  return <div className="console-page"><PageHeader eyebrow={content.eyebrow} title={content.title} description={content.summary} />
    <section className="capability-state"><div className="capability-icon">{content.icon}</div><div><span>当前状态</span><strong>{kind === "alerts" ? "UNCONFIGURED" : "UNAVAILABLE"}</strong><p>这是诚实的能力占位，不显示演示数字，也不把本地模板当成生产能力。</p></div></section>
    <div className="capability-grid"><section className="console-panel"><h2>建设前置条件</h2><ol>{content.blockers.map((item) => <li key={item}>{item}</li>)}</ol></section><section className="console-panel"><h2>规划中的页面能力</h2><ul>{content.next.map((item) => <li key={item}>{item}</li>)}</ul></section></div>
    <section className="console-panel scope-facts"><h2>当前固定范围</h2><dl><div><dt>Tenant</dt><dd>{session.scope.tenantId}</dd></div><div><dt>App</dt><dd>{session.scope.appId}</dd></div><div><dt>Environment</dt><dd>{session.scope.environment}</dd></div><div><dt>Role</dt><dd>{session.role}</dd></div></dl></section>
  </div>;
}
