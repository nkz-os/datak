<script setup lang="ts">
import { RouterView } from 'vue-router'
import { onMounted } from 'vue'
import { useAuthStore } from './stores/auth'
import AppLayout from './components/AppLayout.vue'

const auth = useAuthStore()

onMounted(async () => {
  if (auth.token) {
    await auth.fetchUser()
  }
})
</script>

<template>
  <div class="dark-mode">
    <AppLayout v-if="auth.isAuthenticated">
      <RouterView />
    </AppLayout>
    <RouterView v-else />
  </div>
</template>
