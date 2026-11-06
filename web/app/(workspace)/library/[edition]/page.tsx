import { Suspense } from 'react'
import EditionReader from '@/components/studio/EditionReader'
export default function Page(){return <Suspense fallback={<p>正在打开原文…</p>}><EditionReader/></Suspense>}
