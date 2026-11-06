"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import { ArrowUpRight, BookOpen, FlaskConical, Network, Puzzle, FilePenLine, Layers3, CheckCheck, Telescope, CalendarDays, Sparkles } from "lucide-react";
import { scopedUrl } from "@/lib/workspace-scope";
import { setPendingPrompt } from "@/lib/pending-prompt";
import { useCapabilityFilter } from "@/features/capabilities/useCapabilityCatalog";

const workflows = [
  { mode: "comparison", icon: Layers3, title: "Compare papers", description: "Compare methods, datasets, findings and limitations with traceable evidence.", prompt: "Compare the papers I select. Build an evidence table covering research questions, methods, datasets, main findings, limitations and disagreements. For every claim, give the paper and exact page or section when available. Mark missing evidence as unknown. Distinguish author claims from your interpretation; do not invent citations." },
  { mode: "citation_audit", icon: CheckCheck, title: "Audit citations", description: "Check whether the cited source supports each claim.", prompt: "Audit the claims and references I provide. For each claim, identify the supporting passage, source and locator. Mark supported, contradicted, insufficient evidence or source unavailable. Check source identity and version. Keep unsupported statements visible and propose specific corrections." },
  { mode: "experiment_plan", icon: Telescope, title: "Develop a research question", description: "Turn a topic into a question, evidence gaps and a feasible next experiment.", prompt: "Help develop my research topic. First clarify the question and constraints. Then compare existing evidence, identify concrete gaps and propose a feasible experiment with baselines, metrics and failure criteria. Separate established results, hypotheses and suggestions; cite original sources." },
] as const;
const ecosystem = [
  { icon: BookOpen, title: "Paper library", description: "Read sources and ask questions alongside the document.", href: "/learning/reading" },
  { icon: FilePenLine, title: "Co-Writer", description: "Develop a draft with your notes and research evidence.", href: "/co-writer" },
  { icon: Network, title: "Partners", description: "Keep your existing assistants and collaboration channels.", href: "/partners" },
  { icon: Puzzle, title: "Tools & integrations", description: "Skills, MCP tools and the wider DeepTutor ecosystem.", href: "/space" },
  { icon: CalendarDays, title: "Calendar", description: "Plan follow-ups and review ongoing work.", href: "/calendar" },
  { icon: Sparkles, title: "Visualize", description: "Explain ideas with diagrams and interactive visuals.", href: "/chat?capability=visualize" },
] as const;

export default function ResearchWorkbench() {
  const { t } = useTranslation();
  const router = useRouter();
  const available = useCapabilityFilter();
  const canResearch = available?.("deep_research") ?? false;
  return <main className="mx-auto max-w-6xl px-6 py-10 sm:px-10 sm:py-14">
    <header className="mb-10 max-w-2xl">
      <span className="inline-flex items-center gap-2 text-xs font-semibold tracking-[.16em] text-[var(--primary)]"><FlaskConical size={16}/>{t("DEEPTUTOR · RESEARCH")}</span>
      <h1 className="mt-5 text-3xl font-semibold leading-tight tracking-tight sm:text-4xl">{t("Good questions deserve good evidence.")}</h1>
      <p className="mt-4 max-w-xl text-sm leading-7 text-[var(--muted-foreground)]">{t("A focused place for papers, research and the tools you already trust.")}</p>
      <Link href={scopedUrl("/chat?capability=deep_research")} className="mt-6 inline-flex items-center gap-3 rounded-xl bg-[var(--primary)] px-5 py-3 text-sm font-medium text-[var(--primary-foreground)]">{t("Start research")}<ArrowUpRight size={16}/></Link>
    </header>
    <section aria-label={t("Research workflows")} className="mb-12 grid gap-4 md:grid-cols-3">
      {workflows.map(item => <button key={item.title} type="button" disabled={!canResearch} onClick={() => { setPendingPrompt(t(item.prompt)); router.push(scopedUrl(`/chat?capability=deep_research&research_mode=${item.mode}`)); }} className="group rounded-2xl border border-[var(--border)] bg-[var(--card)] p-6 text-left transition hover:border-[var(--primary)]/50 hover:shadow-md disabled:opacity-50">
        <item.icon size={23} strokeWidth={1.5} className="mb-7 text-[var(--primary)]"/>
        <h2 className="flex items-center justify-between text-base font-semibold">{t(item.title)}<ArrowUpRight size={15} className="opacity-40 group-hover:opacity-100"/></h2>
        <p className="mt-3 text-sm leading-6 text-[var(--muted-foreground)]">{t(item.description)}</p>
      </button>)}
    </section>
    <section aria-labelledby="ecosystem-heading">
      <h2 id="ecosystem-heading" className="mb-5 text-lg font-semibold">{t("Your research ecosystem")}</h2>
      <div className="grid gap-x-8 sm:grid-cols-2 lg:grid-cols-3">{ecosystem.map(item => <Link key={item.href} href={scopedUrl(item.href)} className="group flex gap-4 border-t border-[var(--border)] py-5">
        <item.icon size={20} strokeWidth={1.5} className="mt-1 shrink-0 text-[var(--muted-foreground)]"/>
        <div><h3 className="text-sm font-medium group-hover:text-[var(--primary)]">{t(item.title)}</h3><p className="mt-1.5 text-xs leading-6 text-[var(--muted-foreground)]">{t(item.description)}</p></div>
      </Link>)}</div>
    </section>
  </main>;
}
