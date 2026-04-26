import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

export const useApiStore = defineStore('api', () => {
  const messages = ref<Array<{ role: string; content: string; timestamp?: string }>>([])
  const isLoading = ref(false)
  const connectionStatus = ref({
    deepseek: 'unknown',
    openai: 'unknown',
    database: 'unknown'
  })

  const addMessage = (role: string, content: string) => {
    messages.value.push({
      role,
      content,
      timestamp: new Date().toISOString()
    })
  }

  const sendMessage = async (message: string, model: string = 'deepseek') => {
    isLoading.value = true
    addMessage('user', message)

    try {
      const response = await axios.post(`${API_BASE}/api/chat`, {
        message,
        model
      })

      addMessage('assistant', response.data.response || '收到回复')
      return response.data
    } catch (error) {
      addMessage('assistant', '❌ 后端服务连接失败。请确保 backend 服务正在运行。')
      console.error('Chat error:', error)
    } finally {
      isLoading.value = false
    }
  }

  const testModel = async (model: string) => {
    try {
      const response = await axios.get(`${API_BASE}/api/test/model/${model}`)
      connectionStatus.value[model as keyof typeof connectionStatus.value] = response.data.status
      return response.data
    } catch (error) {
      connectionStatus.value[model as keyof typeof connectionStatus.value] = 'error'
      console.error(`Test ${model} error:`, error)
      return { status: 'error', message: '连接失败' }
    }
  }

  const testDatabase = async () => {
    try {
      const response = await axios.get(`${API_BASE}/api/test/database`)
      connectionStatus.value.database = 'success'
      return response.data
    } catch (error) {
      connectionStatus.value.database = 'error'
      console.error('Database test error:', error)
      return { status: 'error', message: '数据库测试失败' }
    }
  }

  const testAll = async () => {
    await testModel('deepseek')
    await testModel('openai')
    await testDatabase()
  }

  const clearMessages = () => {
    messages.value = []
  }

  return {
    messages,
    isLoading,
    connectionStatus,
    sendMessage,
    testModel,
    testDatabase,
    testAll,
    addMessage,
    clearMessages,
    API_BASE
  }
})
