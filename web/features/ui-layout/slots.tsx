"use client";

import { useUiLayout } from "@/hooks/useUiLayout";
import type { UiLayout } from "@/lib/ui-layout";
import dynamic from "next/dynamic";
import { modernSlots } from "./modern";
import type { SidebarShellProps, UiLayoutSlots } from "./types";

const UI_LAYOUT_SLOTS: Record<UiLayout, UiLayoutSlots> = {
  // Keep the preserved classic interface available without loading both
  // complete sidebars on every modern workspace route.
  classic: {
    SidebarShell: dynamic(() =>
      import("./classic").then((module) => module.classicSlots.SidebarShell),
    ),
    PageBar: null,
    LearningHome: dynamic(() =>
      import("./classic/ClassicLearningDashboard").then(
        (module) => module.ClassicLearningDashboard,
      ),
    ),
  },
  modern: modernSlots,
};

function useSlots(): UiLayoutSlots {
  return UI_LAYOUT_SLOTS[useUiLayout()];
}

// Shared code renders these and never the variants' components directly.

export function LayoutSidebarShell(props: SidebarShellProps) {
  const { SidebarShell } = useSlots();
  return <SidebarShell {...props} />;
}

export function LayoutPageBar() {
  const { PageBar } = useSlots();
  return PageBar ? <PageBar /> : null;
}

export function LayoutLearningHome() {
  const { LearningHome } = useSlots();
  return <LearningHome />;
}
