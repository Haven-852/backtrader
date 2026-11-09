<template>
  <div class="min-h-full flex items-center justify-center bg-[#14110c] px-4 py-10">
    <div class="w-full max-w-md rounded-2xl border border-[#2a251c] bg-[#1a1610]/90 p-6 shadow-xl">
      <button type="button" class="text-xs text-[#8a8070] hover:text-[#c4a35a] mb-4" @click="emit('back')">← 返回介绍页</button>
      <div class="flex items-center gap-3 mb-6">
        <div class="w-10 h-10 rounded-2xl bg-[#c4a35a] text-[#14110c] font-bold flex items-center justify-center" style="font-family: Fraunces, Georgia, serif">A</div>
        <div>
          <h1 class="text-lg font-semibold text-[#f3ede6]" style="font-family: Fraunces, Georgia, serif">AIquant 登录</h1>
          <p class="text-xs text-[#8a8070]">订阅版 MVP · 对话需登录后使用</p>
        </div>
      </div>

      <div class="flex gap-2 mb-4">
        <button
          type="button"
          class="flex-1 py-2 rounded-lg text-sm"
          :class="mode === 'login' ? 'bg-[#c4a35a] text-[#14110c]' : 'bg-[#241f18] text-[#8a8070]'"
          @click="mode = 'login'"
        >登录</button>
        <button
          type="button"
          class="flex-1 py-2 rounded-lg text-sm"
          :class="mode === 'register' ? 'bg-[#c4a35a] text-[#14110c]' : 'bg-[#241f18] text-[#8a8070]'"
          @click="mode = 'register'"
        >注册</button>
      </div>

      <form class="space-y-3" @submit.prevent="onSubmit">
        <label class="block text-xs text-[#8a8070]">
          邮箱
          <input v-model="email" type="email" required class="mt-1 w-full rounded-lg bg-[#14110c] border border-[#3a3226] px-3 py-2 text-sm text-[#f3ede6] outline-none focus:border-[#c4a35a]" placeholder="you@example.com" />
        </label>
        <label class="block text-xs text-[#8a8070]">
          密码
          <input v-model="password" type="password" required minlength="6" class="mt-1 w-full rounded-lg bg-[#14110c] border border-[#3a3226] px-3 py-2 text-sm text-[#f3ede6] outline-none focus:border-[#c4a35a]" placeholder="至少 6 位" />
        </label>
        <label v-if="mode === 'register'" class="block text-xs text-[#8a8070]">
          显示名（可选）
          <input v-model="displayName" type="text" class="mt-1 w-full rounded-lg bg-[#14110c] border border-[#3a3226] px-3 py-2 text-sm text-[#f3ede6] outline-none focus:border-[#c4a35a]" />
        </label>
        <label v-if="mode === 'register'" class="block text-xs text-[#8a8070]">
          邀请码（若管理员已启用）
          <input v-model="inviteCode" type="text" class="mt-1 w-full rounded-lg bg-[#14110c] border border-[#3a3226] px-3 py-2 text-sm text-[#f3ede6] outline-none focus:border-[#c4a35a]" />
        </label>

        <p v-if="error" class="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{{ error }}</p>
        <p v-if="hint" class="text-[11px] text-[#5c5548] leading-relaxed">{{ hint }}</p>

        <button
          type="submit"
          class="w-full py-2.5 rounded-xl bg-[#c4a35a] hover:bg-[#d4b56a] text-[#14110c] text-sm font-medium disabled:opacity-50"
          :disabled="busy"
        >
          <i v-if="busy" class="fa-solid fa-spinner animate-spin mr-1"></i>
          {{ mode === 'login' ? '登录' : '注册并登录' }}
        </button>
      </form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useAuth } from './authStore'

const emit = defineEmits<{ (e: 'back'): void }>()
const { login, register, busy, error } = useAuth()
const mode = ref<'login' | 'register'>('login')
const email = ref('')
const password = ref('')
const displayName = ref('')
const inviteCode = ref('')
const hint = ref('第一个注册的账号自动成为管理员（pro）。之后注册可能需要邀请码 AUTH_INVITE_CODE。')

async function onSubmit() {
  if (mode.value === 'login') {
    await login(email.value.trim(), password.value)
  } else {
    await register({
      email: email.value.trim(),
      password: password.value,
      display_name: displayName.value.trim() || undefined,
      invite_code: inviteCode.value.trim() || undefined,
    })
  }
}
</script>
