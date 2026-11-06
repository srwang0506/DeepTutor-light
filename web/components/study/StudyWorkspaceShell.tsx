"use client";

import type { ComponentPropsWithRef } from "react";

/** Layout only. Session, reading, mastery and draft owners stay mounted below. */
export function StudyWorkspaceShell({ className = "", children, ...props }: ComponentPropsWithRef<"main">) {
  return <main {...props} data-study-workspace="" className={`study-workspace ${className}`}>{children}</main>;
}
