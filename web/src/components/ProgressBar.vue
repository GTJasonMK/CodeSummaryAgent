<template>
  <div class="w-full">
    <!-- 进度信息 -->
    <div class="flex items-center justify-between text-sm mb-2">
      <span class="text-gray-600">{{ label }}</span>
      <span class="font-medium" :class="percentageClass">{{ percentage.toFixed(1) }}%</span>
    </div>

    <!-- 进度条 -->
    <div class="w-full bg-gray-200 rounded-full overflow-hidden" :style="{ height: `${height}px` }">
      <div
        class="h-full rounded-full transition-all duration-500 ease-out"
        :class="barClass"
        :style="{ width: `${percentage}%` }"
      ></div>
    </div>

    <!-- 详细信息 -->
    <div v-if="showDetails" class="flex items-center justify-between mt-2 text-xs text-gray-500">
      <span>{{ current }} / {{ total }}</span>
      <span v-if="eta">预计剩余: {{ eta }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  current: {
    type: Number,
    default: 0,
  },
  total: {
    type: Number,
    default: 100,
  },
  label: {
    type: String,
    default: '进度',
  },
  height: {
    type: Number,
    default: 8,
  },
  showDetails: {
    type: Boolean,
    default: false,
  },
  status: {
    type: String,
    default: 'normal', // normal, success, error, warning
  },
  eta: {
    type: String,
    default: '',
  },
})

const percentage = computed(() => {
  if (props.total === 0) return 0
  return Math.min(100, (props.current / props.total) * 100)
})

const barClass = computed(() => {
  const map = {
    normal: 'bg-primary-500',
    success: 'bg-green-500',
    error: 'bg-red-500',
    warning: 'bg-yellow-500',
  }
  return map[props.status] || 'bg-primary-500'
})

const percentageClass = computed(() => {
  const map = {
    normal: 'text-primary-600',
    success: 'text-green-600',
    error: 'text-red-600',
    warning: 'text-yellow-600',
  }
  return map[props.status] || 'text-primary-600'
})
</script>
