import { useQuery } from '@tanstack/react-query'
import { fetchQueue } from '@/api/queue'

export const QUEUE_KEY = ['queue'] as const

export function useQueue() {
  return useQuery({ queryKey: QUEUE_KEY, queryFn: fetchQueue })
}
