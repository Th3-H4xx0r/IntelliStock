<script setup>
// Older, unlinked copy of the landing page (route /animation). It shares the
// landing sections and hero; only its two actions differ.
import { useRouter } from 'vue-router'
import TheNavbar from '../components/TheNavbar.vue'
import TheFooter from '../components/TheFooter.vue'
import HeroSection from '../components/landing/HeroSection.vue'
import StrategySection from '../components/landing/StrategySection.vue'
import NexusSection from '../components/landing/NexusSection.vue'
import BacktestSection from '../components/landing/BacktestSection.vue'
import DeploySection from '../components/landing/DeploySection.vue'

const router = useRouter()
</script>

<template>
  <div class="min-h-screen bg-background-dark text-slate-100 overflow-x-hidden">
    <TheNavbar />

    <HeroSection>
      <template #actions>
        <button type="button" class="ui-btn-primary h-11 px-5" @click="router.push('/login')">Get started</button>
        <button type="button" class="ui-btn h-11 px-5 text-sm" @click="router.push('/')">View strategies</button>
      </template>
    </HeroSection>

    <main>
      <StrategySection />
      <NexusSection />
      <BacktestSection />
      <DeploySection />
    </main>

    <TheFooter />
  </div>
</template>
