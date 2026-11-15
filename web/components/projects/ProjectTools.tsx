'use client'

import { useState } from 'react'
import dynamic from 'next/dynamic'
import { ChevronRight, Settings2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import '../studio/studio.css'

function LoadingTool() {
  const { t } = useTranslation()
  return (
    <p role="status" className="project-help">
      {t('正在加载项目工具…')}
    </p>
  )
}

const PortableWorkspace = dynamic(() => import('../studio/PortableWorkspace'), {
  loading: LoadingTool,
})
const EvaluationPanel = dynamic(() => import('../studio/EvaluationPanel'), { loading: LoadingTool })

export default function ProjectTools({ workspace }: { workspace: string }) {
  const { t } = useTranslation()
  const [loaded, setLoaded] = useState(false)
  return (
    <details
      className="project-panel project-tools"
      onToggle={event => {
        if (event.currentTarget.open) setLoaded(true)
      }}
    >
      <summary>
        <Settings2 size={17} aria-hidden />
        <span>
          <strong>{t('项目工具')}</strong>
          <small>{t('迁移项目资料，或运行并复核模型评测。')}</small>
        </span>
        <ChevronRight size={16} className="project-tools-chevron" aria-hidden />
      </summary>
      {loaded && (
        <div className="studio-section-stack project-tools-content">
          <PortableWorkspace workspace={workspace} />
          <EvaluationPanel workspace={workspace} />
        </div>
      )}
    </details>
  )
}
