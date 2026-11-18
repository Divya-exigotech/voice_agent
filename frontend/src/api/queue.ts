import { apiClient } from '@/lib/apiClient'
import type { QueueState, SessionRecord } from '@/types/queue'

export async function fetchQueue(): Promise<QueueState> {
  const { data } = await apiClient.get<QueueState>('/queue')
  return data
}

export async function createSession(): Promise<SessionRecord> {
  const { data } = await apiClient.post<SessionRecord>('/sessions')
  return data
}

export async function finalizeSession(sessionId: string): Promise<QueueState> {
  const { data } = await apiClient.post<QueueState>(`/sessions/${sessionId}/finalize`)
  return data
}
