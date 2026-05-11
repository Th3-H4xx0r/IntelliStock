// v-reveal directive — adds 'is-visible' when element enters the viewport.
// Usage: <div v-reveal class="reveal [from-left|from-right|from-scale] [delay-100…500]">
const scrollReveal = {
  mounted(el) {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          el.classList.add('is-visible')
          observer.unobserve(el)
        }
      },
      { threshold: 0.1, rootMargin: '0px 0px -60px 0px' }
    )
    observer.observe(el)
  },
}

export default scrollReveal
