import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api } from '@/api'

export const useAnalysisStore = defineStore('analysis', () => {
  // 状态
  const tasks = ref([])
  const currentTask = ref(null)
  const fileTree = ref(null)
  const isLoading = ref(false)
  const error = ref(null)

  // WebSocket连接
  const ws = ref(null)
  const wsConnected = ref(false)

  // 计算属性
  const runningTasks = computed(() =>
    tasks.value.filter(t => t.status === 'running')
  )

  const completedTasks = computed(() =>
    tasks.value.filter(t => t.status === 'completed')
  )

  // 操作方法
  async function fetchTasks() {
    isLoading.value = true
    try {
      const data = await api.getTasks()
      tasks.value = data
    } catch (e) {
      error.value = e.message
    } finally {
      isLoading.value = false
    }
  }

  async function fetchTask(taskId) {
    isLoading.value = true
    try {
      currentTask.value = await api.getTask(taskId)
    } catch (e) {
      error.value = e.message
    } finally {
      isLoading.value = false
    }
  }

  async function startAnalysis(sourcePath, options = {}) {
    isLoading.value = true
    error.value = null
    try {
      const result = await api.startAnalysis(sourcePath, options)
      await fetchTasks()

      // 连接WebSocket订阅进度
      if (result.task_id) {
        connectWebSocket(result.task_id)
      }

      return result
    } catch (e) {
      error.value = e.message
      throw e
    } finally {
      isLoading.value = false
    }
  }

  async function scanDirectory(sourcePath) {
    isLoading.value = true
    try {
      const result = await api.scanDirectory(sourcePath)
      fileTree.value = result.root
      return result
    } catch (e) {
      error.value = e.message
      throw e
    } finally {
      isLoading.value = false
    }
  }

  async function cancelTask(taskId) {
    try {
      await api.cancelTask(taskId)
      await fetchTasks()
    } catch (e) {
      error.value = e.message
    }
  }

  // WebSocket
  function connectWebSocket(taskId) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}/api/ws/${taskId}`

    ws.value = new WebSocket(wsUrl)

    ws.value.onopen = () => {
      wsConnected.value = true
      console.log('WebSocket connected')
    }

    ws.value.onmessage = (event) => {
      const message = JSON.parse(event.data)
      handleWebSocketMessage(message)
    }

    ws.value.onclose = () => {
      wsConnected.value = false
      console.log('WebSocket disconnected')
    }

    ws.value.onerror = (error) => {
      console.error('WebSocket error:', error)
    }
  }

  function handleWebSocketMessage(message) {
    const { type, data } = message

    switch (type) {
      case 'analysis_progress':
        if (currentTask.value && currentTask.value.task_id === data.task_id) {
          currentTask.value.progress = data.percentage
          currentTask.value.current_file = data.current_file
        }
        break

      case 'analysis_complete':
        fetchTasks()
        if (currentTask.value && currentTask.value.task_id === data.task_id) {
          currentTask.value.status = 'completed'
          currentTask.value.stats = data.stats
        }
        break

      case 'analysis_error':
        if (currentTask.value && currentTask.value.task_id === data.task_id) {
          currentTask.value.status = 'failed'
          currentTask.value.error = data.error
        }
        break

      case 'analysis_file_complete':
        // 更新文件树状态
        if (fileTree.value) {
          updateNodeStatus(fileTree.value, data.file_path, 'completed')
        }
        break

      case 'analysis_file_failed':
        if (fileTree.value) {
          updateNodeStatus(fileTree.value, data.file_path, 'failed')
        }
        break
    }
  }

  function updateNodeStatus(node, filePath, status) {
    if (node.relative_path === filePath) {
      node.status = status
      return true
    }
    for (const child of node.children || []) {
      if (updateNodeStatus(child, filePath, status)) {
        return true
      }
    }
    return false
  }

  function disconnectWebSocket() {
    if (ws.value) {
      ws.value.close()
      ws.value = null
    }
  }

  return {
    // 状态
    tasks,
    currentTask,
    fileTree,
    isLoading,
    error,
    wsConnected,

    // 计算属性
    runningTasks,
    completedTasks,

    // 方法
    fetchTasks,
    fetchTask,
    startAnalysis,
    scanDirectory,
    cancelTask,
    connectWebSocket,
    disconnectWebSocket,
  }
})
