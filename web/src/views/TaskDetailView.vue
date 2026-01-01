<template>
  <div class="space-y-6" v-if="task">
    <!-- 返回按钮 -->
    <button
      @click="router.push('/tasks')"
      class="flex items-center space-x-2 text-gray-600 hover:text-gray-800"
    >
      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
      </svg>
      <span>返回任务列表</span>
    </button>

    <!-- 任务信息卡片 -->
    <div class="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
      <div class="flex items-center justify-between mb-4">
        <div>
          <h2 class="text-xl font-semibold text-gray-800">{{ getPathName(task.source_path) }}</h2>
          <p class="text-sm text-gray-500 mt-1">{{ task.source_path }}</p>
        </div>

        <div class="flex items-center space-x-3">
          <span
            class="px-3 py-1 rounded-full text-sm font-medium"
            :class="getStatusClass(task.status)"
          >
            {{ getStatusText(task.status) }}
          </span>

          <button
            v-if="task.status === 'running'"
            @click="handleCancel"
            class="px-3 py-1.5 text-sm text-red-600 hover:bg-red-50 rounded-lg"
          >
            取消
          </button>
        </div>
      </div>

      <!-- 进度条 -->
      <div class="mb-6">
        <div class="flex items-center justify-between text-sm text-gray-600 mb-2">
          <span>分析进度</span>
          <span>{{ task.progress.toFixed(1) }}%</span>
        </div>
        <div class="w-full bg-gray-200 rounded-full h-3">
          <div
            class="h-3 rounded-full transition-all duration-500"
            :class="task.status === 'failed' ? 'bg-red-500' : 'bg-primary-500'"
            :style="{ width: `${task.progress}%` }"
          ></div>
        </div>
      </div>

      <!-- 统计信息 -->
      <div class="grid grid-cols-4 gap-4">
        <div class="bg-gray-50 rounded-lg p-4 text-center">
          <div class="text-2xl font-bold text-gray-800">{{ task.total_files }}</div>
          <div class="text-sm text-gray-500">总文件数</div>
        </div>
        <div class="bg-green-50 rounded-lg p-4 text-center">
          <div class="text-2xl font-bold text-green-600">{{ task.processed_files }}</div>
          <div class="text-sm text-gray-500">已处理</div>
        </div>
        <div class="bg-red-50 rounded-lg p-4 text-center">
          <div class="text-2xl font-bold text-red-600">{{ task.failed_files }}</div>
          <div class="text-sm text-gray-500">失败</div>
        </div>
        <div class="bg-blue-50 rounded-lg p-4 text-center">
          <div class="text-2xl font-bold text-blue-600">
            {{ task.total_files - task.processed_files - task.failed_files }}
          </div>
          <div class="text-sm text-gray-500">待处理</div>
        </div>
      </div>

      <!-- 错误信息 -->
      <div v-if="task.error" class="mt-4 bg-red-50 border border-red-200 rounded-lg p-4">
        <div class="flex items-center space-x-2 text-red-700">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <span>{{ task.error }}</span>
        </div>
      </div>

      <!-- 文档输出路径 -->
      <div v-if="task.docs_path" class="mt-4 bg-gray-50 rounded-lg p-4">
        <div class="text-sm text-gray-500 mb-1">文档输出目录</div>
        <div class="font-mono text-sm text-gray-700">{{ task.docs_path }}</div>
      </div>
    </div>

    <!-- 文件树 -->
    <div v-if="fileTree" class="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
      <h3 class="text-lg font-semibold text-gray-800 mb-4">文件分析状态</h3>
      <FileTree :node="fileTree" :showStatus="true" />
    </div>

    <!-- 时间信息 -->
    <div class="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
      <h3 class="text-lg font-semibold text-gray-800 mb-4">时间信息</h3>
      <div class="space-y-2 text-sm">
        <div class="flex justify-between">
          <span class="text-gray-500">创建时间</span>
          <span class="text-gray-700">{{ formatTime(task.created_at) }}</span>
        </div>
        <div v-if="task.started_at" class="flex justify-between">
          <span class="text-gray-500">开始时间</span>
          <span class="text-gray-700">{{ formatTime(task.started_at) }}</span>
        </div>
        <div v-if="task.completed_at" class="flex justify-between">
          <span class="text-gray-500">完成时间</span>
          <span class="text-gray-700">{{ formatTime(task.completed_at) }}</span>
        </div>
      </div>
    </div>
  </div>

  <!-- 加载状态 -->
  <div v-else class="flex items-center justify-center py-20">
    <svg class="animate-spin h-8 w-8 text-primary-500" fill="none" viewBox="0 0 24 24">
      <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
      <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
    </svg>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAnalysisStore } from '@/stores/analysis'
import FileTree from '@/components/FileTree.vue'
import { api } from '@/api'

const route = useRoute()
const router = useRouter()
const store = useAnalysisStore()

const task = computed(() => store.currentTask)
const fileTree = ref(null)

let refreshInterval = null

onMounted(async () => {
  const taskId = route.params.id
  await store.fetchTask(taskId)

  // 连接WebSocket
  if (task.value?.status === 'running') {
    store.connectWebSocket(taskId)
  }

  // 获取文件树
  try {
    const result = await api.getTaskTree(taskId)
    fileTree.value = result.root
  } catch (e) {
    console.log('获取文件树失败:', e)
  }

  // 定时刷新运行中的任务
  refreshInterval = setInterval(() => {
    if (task.value?.status === 'running') {
      store.fetchTask(taskId)
    }
  }, 3000)
})

onUnmounted(() => {
  store.disconnectWebSocket()
  if (refreshInterval) {
    clearInterval(refreshInterval)
  }
})

function getPathName(path) {
  return path?.split(/[/\\]/).pop() || path
}

function getStatusText(status) {
  const map = {
    pending: '等待中',
    running: '运行中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
  }
  return map[status] || status
}

function getStatusClass(status) {
  const map = {
    pending: 'bg-gray-100 text-gray-700',
    running: 'bg-blue-100 text-blue-700',
    completed: 'bg-green-100 text-green-700',
    failed: 'bg-red-100 text-red-700',
    cancelled: 'bg-gray-100 text-gray-700',
  }
  return map[status] || 'bg-gray-100 text-gray-700'
}

function formatTime(timeStr) {
  if (!timeStr) return '-'
  const date = new Date(timeStr)
  return date.toLocaleString('zh-CN')
}

async function handleCancel() {
  if (confirm('确定要取消此任务吗？')) {
    await store.cancelTask(route.params.id)
  }
}
</script>
