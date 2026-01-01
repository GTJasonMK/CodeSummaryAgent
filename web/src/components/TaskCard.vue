<template>
  <div class="bg-white rounded-lg border border-gray-200 p-4">
    <div class="flex items-center justify-between">
      <div class="flex items-center space-x-3">
        <!-- 状态图标 -->
        <div
          class="w-12 h-12 rounded-full flex items-center justify-center"
          :class="getStatusBgClass"
        >
          <svg v-if="task.status === 'completed'" class="w-6 h-6 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
          </svg>
          <svg v-else-if="task.status === 'failed'" class="w-6 h-6 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
          <svg v-else-if="task.status === 'running'" class="w-6 h-6 text-blue-600 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          <svg v-else class="w-6 h-6 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        </div>

        <div>
          <div class="font-medium text-gray-800">{{ getPathName(task.source_path) }}</div>
          <div class="text-sm text-gray-500 truncate max-w-md">{{ task.source_path }}</div>
        </div>
      </div>

      <div class="flex items-center space-x-3">
        <span
          class="px-3 py-1 rounded-full text-xs font-medium"
          :class="getStatusClass"
        >
          {{ getStatusText }}
        </span>

        <button
          v-if="task.status === 'running'"
          @click.stop="$emit('cancel')"
          class="p-2 text-red-500 hover:bg-red-50 rounded-lg"
          title="取消任务"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M9 10a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1v-4z" />
          </svg>
        </button>
      </div>
    </div>

    <!-- 进度条 -->
    <div v-if="task.status === 'running'" class="mt-4">
      <div class="flex items-center justify-between text-sm mb-1">
        <span class="text-gray-500">{{ task.current_file || '处理中...' }}</span>
        <span class="font-medium text-gray-700">{{ task.progress.toFixed(1) }}%</span>
      </div>
      <div class="w-full bg-gray-200 rounded-full h-2">
        <div
          class="bg-primary-500 h-2 rounded-full transition-all duration-300"
          :style="{ width: `${task.progress}%` }"
        ></div>
      </div>
    </div>

    <!-- 统计信息 -->
    <div v-if="task.status !== 'pending'" class="mt-3 flex items-center space-x-4 text-xs text-gray-500">
      <span>
        <span class="font-medium text-gray-700">{{ task.processed_files }}</span>
        / {{ task.total_files }} 文件
      </span>
      <span v-if="task.failed_files > 0" class="text-red-500">
        {{ task.failed_files }} 失败
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  task: {
    type: Object,
    required: true,
  },
})

defineEmits(['cancel'])

function getPathName(path) {
  return path.split(/[/\\]/).pop() || path
}

const getStatusText = computed(() => {
  const map = {
    pending: '等待中',
    running: '运行中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
  }
  return map[props.task.status] || props.task.status
})

const getStatusBgClass = computed(() => {
  const map = {
    pending: 'bg-gray-100',
    running: 'bg-blue-100',
    completed: 'bg-green-100',
    failed: 'bg-red-100',
    cancelled: 'bg-gray-100',
  }
  return map[props.task.status] || 'bg-gray-100'
})

const getStatusClass = computed(() => {
  const map = {
    pending: 'bg-gray-100 text-gray-700',
    running: 'bg-blue-100 text-blue-700',
    completed: 'bg-green-100 text-green-700',
    failed: 'bg-red-100 text-red-700',
    cancelled: 'bg-gray-100 text-gray-700',
  }
  return map[props.task.status] || 'bg-gray-100 text-gray-700'
})
</script>
