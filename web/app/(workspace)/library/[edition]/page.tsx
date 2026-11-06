import StudioLoading from '@/components/studio/StudioLoading'
import { Suspense } from 'react'
import EditionReader from '@/components/studio/EditionReader'
export default function Page() {
  return (
    <Suspense fallback={<StudioLoading label="正在打开原文…" />}>
      <EditionReader />
    </Suspense>
  )
}
