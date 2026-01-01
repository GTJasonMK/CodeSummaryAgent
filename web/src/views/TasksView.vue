<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <h2 class="text-xl font-semibold text-gray-800">任务列表</h2>
      <button
        @click="store.fetchTasks"
        class="px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg"
      >
        刷新
      </button>
    </div>

    <!-- 任务列表 -->
    <div v-if="tasks.length > 0" class="space-y-4">
      <div
        v-for="task in tasks"
        :key="task.task_id"
        class="bg-white rounded-lg shadow-sm border border-gray-200 p-4 hover:shadow-md transition-shadow cursor-pointer"
        @click="router.push(`/task/${task.task_id}`)"
      >
        <div class="flex items-center justify-between">
          <div class="flex items-center space-x-3">
            <!-- 状态图标 -->
            <div
              class="w-10 h-10 rounded-full flex items-center justify-center"
              :class="getStatusBgClass(task.status)"
            >
              <svg v-if="task.status === 'completed'" class="w-5 h-5 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
              </svg>
              <svg v-else-if="task.status === 'failed'" class="w-5 h-5 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
              <svg v-else-if="task.status === 'running'" class="w-5 h-5 text-blue-600 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
              <svg v-else class="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>

            <div>
              <div class="font-medium text-gray-800">{{ getPathName(task.source_path) }}</div>
              <div class="text-sm text-gray-500">{{ task.source_path }}</div>
            </div>
          </div>

          <div class="text-right">
            <div class="text-sm font-medium" :class="getStatusTextClass(task.status)">
              {{ getStatusText(task.status) }}
            </div>
            <div class="text-xs text-gray-400">{{ formatTime(task.created_at) }}</div>
          </div>
        </div>

        <!-- 进度条 -->
        <div v-if="task.status === 'running'" class="mt-3">
          <div class="flex items-center justify-between text-sm text-gray-600 mb-1">
            <span>进度</span>
            <span>{{ task.progress.toFixed(1) }}%</span>
          </div>
          <div class="w-full bg-gray-200 rounded-full h-2">
            <div
              class="bg-primary-500 h-2 rounded-full transition-all duration-300"
              :style="{ width: `${task.progress}%` }"
            ></div>
          </div>
        </div>

        <!-- 统计信息 -->
        <div v-if="task.status === 'completed'" class="mt-3 flex items-center space-x-4 text-sm text-gray-500">
          <span>文件: {{ task.processed_files }}/{{ task.total_files }}</span>
          <span v-if="task.failed_files > 0" class="text-red-500">失败: {{ task.failed_files }}</span>
        </div>
      </div>
    </div>

    <!-- 空状态 -->
    <div v-else class="bg-white rounded-lg shadow-sm border border-gray-200 p-12 text-center">
      <svg class="w-16 h-16 text-gray-300 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
      </svg>
      <p class="text-gray-500">暂无任务</p>
      <button
        @click="router.push('/')"
        class="mt-4 px-4 py-2 bg-primary-500 text-white rounded-lg hover:bg-primary-600"
      >
        创建新任务
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAnalysisStore } from '@/stores/analysis'

const router = useRouter()
const store = useAnalysisStore()

const tasks = computed(() => store.tasks)

onMounted(() => {
  store.fetchTasks()
})

function getPathName(path) {
  return path.split(/[/\\]/).pop() || path
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

function getStatusBgClass(status) {
  const map = {
    pending: 'bg-gray-100',
    running: 'bg-blue-100',
    completed: 'bg-green-100',
    failed: 'bg-red-100',
    cancelled: 'bg-gray-100',
  }
  return map[status] || 'bg-gray-100'
}

function getStatusTextClass(status) {
  const map = {
    pending: 'text-gray-600',
    running: 'text-blue-600',
    completed: 'text-green-600',
    failed: 'text-red-600',
    cancelled: 'text-gray-600',
  }
  return map[status] || 'text-gray-600'
}

function formatTime(timeStr) {
  if (!timeStr) return ''
  const date = new Date(timeStr)
  return date.toLocaleString('zh-CN')
}
</script>
