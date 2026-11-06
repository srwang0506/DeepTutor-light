import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { initI18n } from '@/i18n/init';
import StudyQuizViewer from '@/components/quiz/StudyQuizViewer';

initI18n('en');
const mocks = vi.hoisted(() => ({ drafts: new Map<string, unknown>(), fetch: vi.fn(), send: vi.fn() }));
vi.mock('@/components/common/MarkdownRenderer', () => ({ default: ({content}:{content:string}) => <>{content}</> }));
vi.mock('@/features/chat/ChatStateAdapter', () => ({ useChatStateAdapter: () => ({ sendMessage: mocks.send, state: {isStreaming:false, enabledTools:[]} }) }));
vi.mock('@/lib/api', () => ({ apiFetch: mocks.fetch }));
vi.mock('@/lib/workspace-scope', () => ({ activeWorkspaceId: ()=>'workspace', scopedUrl: (url:string)=>url }));
vi.mock('@/lib/workspace-drafts', () => ({
  readWorkspaceDraft: async (_w:string, p:string) => mocks.drafts.get(p),
  saveWorkspaceDraft: async (v:unknown, _w:string, p:string) => {mocks.drafts.set(p,v);},
}));
const question = {question_id:'q1', question_version:'v1', question:'Explain acceleration', question_type:'short_answer' as const, correct_answer:'Change of velocity', explanation:'Velocity per unit time', difficulty:'easy', answerable:true};
const receipt = {attempt_id:'saved-attempt-id', state:'committed', result:'correct', assessment_committed:true, feedback:'Well explained', projection_status:'complete'};
beforeEach(()=>{mocks.drafts.clear();mocks.fetch.mockReset();mocks.send.mockReset();});

it('loads a reference only through the disclosure endpoint after opening a redacted question', async()=>{
  const {correct_answer: _answer, explanation: _explanation, ...redacted} = question;
  let release: (value: unknown) => void = () => {};
  mocks.fetch.mockImplementation(async(url:string, options?:RequestInit)=>{
    expect(url).toBe('/api/quiz/exposures');
    expect(JSON.parse(options?.body as string).level).toBe('revealed');
    return await new Promise(resolve=>{release=resolve;});
  });
  render(<StudyQuizViewer questions={[redacted]} sessionId="s" turnId="t"/>);
  await waitFor(()=>expect(screen.getByRole('textbox')).toBeEnabled());
  expect(screen.queryByText(/Change of velocity/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Show reference'}));
  await waitFor(()=>expect(mocks.fetch).toHaveBeenCalledOnce());
  expect(screen.queryByText(/Change of velocity/)).not.toBeInTheDocument();
  release({ok:true,json:async()=>({recorded:true,correct_answer:'Change of velocity',explanation:'Velocity per unit time'})});
  expect(await screen.findByText(/Change of velocity/)).toBeVisible();
  await waitFor(()=>expect(JSON.stringify([...mocks.drafts.values()])).toContain('revealed'));
});

it('restores a submitted draft and reads its committed receipt without resubmitting', async()=>{
  mocks.drafts.set('/quiz/s/t/q1/v1',{text:JSON.stringify({answer:'Change in velocity', attemptId:'saved-attempt-id', assistance:'independent',submitted:true}),attachments:[]});
  mocks.fetch.mockResolvedValue({ok:true,json:async()=>receipt});
  render(<StudyQuizViewer questions={[question]} sessionId="s" turnId="t"/>);
  expect(await screen.findByText('Correct')).toBeVisible();
  expect(screen.getByRole('textbox')).toHaveValue('Change in velocity');
  expect(mocks.fetch).toHaveBeenCalledOnce();
  expect(mocks.fetch.mock.calls[0][0]).toBe('/api/quiz/attempts/s/saved-attempt-id');
  expect(screen.queryByRole('button',{name:'Submit answer'})).not.toBeInTheDocument();
});

it('persists identity before submit, sends no answer key, and recovers a lost response', async()=>{
  mocks.fetch.mockImplementation(async(url:string, options?:RequestInit)=>{
    if(url.endsWith('/submit')) {
      expect(mocks.drafts.size).toBe(1);
      const payload=JSON.parse(options?.body as string);
      expect(payload.answer).toBe('Velocity changes over time');
      expect(payload.question_version).toBe('v1');
      expect(payload.correct_answer).toBeUndefined();
      throw Error('connection lost after commit');
    }
    return {ok:true,json:async()=>receipt};
  });
  render(<StudyQuizViewer questions={[question]} sessionId="s" turnId="t"/>);
  await waitFor(()=>expect(screen.getByRole('textbox')).toBeEnabled());
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'Velocity changes over time'}});
  fireEvent.click(screen.getByRole('button',{name:'Submit answer'}));
  expect(await screen.findByText('Correct')).toBeVisible();
  expect(mocks.fetch.mock.calls.filter(([url])=>url.endsWith('/submit'))).toHaveLength(1);
});

it('keeps drafts mounted across questions and never makes failed generation answerable', async()=>{
  render(<StudyQuizViewer questions={[question,{...question,question_id:'q2', question:'',answerable:false,generation_status:'failed',issues:['missing_answer']}]} sessionId="s" turnId="t"/>);
  await waitFor(()=>expect(screen.getByRole('textbox')).toBeEnabled());
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'Unfinished working'}});
  fireEvent.click(screen.getByRole('button',{name:'Next'}));
  expect(screen.getByText('Question generation failed')).toBeVisible();
  expect(screen.queryByRole('button',{name:'Submit answer'})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Previous'}));
  expect(screen.getByRole('textbox')).toHaveValue('Unfinished working');
});
