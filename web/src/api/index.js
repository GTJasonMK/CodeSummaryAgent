const API_BASE = '/api'

async function request(url, options = {}) {
  const response = await fetch(`${API_BASE}${url}`, {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new Error(error.detail || `HTTP Error: ${response.status}`)
  }

  return response.json()
}

export const api = {
  // 健康检查
  async healthCheck() {
    return request('/health')
  },

  // 获取任务列表
  async getTasks() {
    return request('/tasks')
  },

  // 获取任务详情
  async getTask(taskId) {
    return request(`/tasks/${taskId}`)
  },

  // 启动分析
  async startAnalysis(sourcePath, options = {}) {
    return request('/analyze', {
      method: 'POST',
      body: JSON.stringify({
        source_path: sourcePath,
        docs_path: options.docsPath,
        resume: options.resume !== false,
        config_overrides: options.configOverrides,
      }),
    })
  },

  // 扫描目录
  async scanDirectory(sourcePath) {
    return request('/scan', {
      method: 'POST',
      body: JSON.stringify({
        source_path: sourcePath,
      }),
    })
  },

  // 取消任务
  async cancelTask(taskId) {
    return request(`/tasks/${taskId}/cancel`, {
      method: 'POST',
    })
  },

  // 获取任务文件树
  async getTaskTree(taskId) {
    return request(`/tasks/${taskId}/tree`)
  },

  // 获取配置
  async getConfig() {
    return request('/config')
  },

  // 更新配置
  async updateConfig(config) {
    return request('/config', {
      method: 'PUT',
      body: JSON.stringify(config),
    })
  },
}
