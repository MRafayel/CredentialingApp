import { AnimatePresence, MotionConfig, motion } from 'motion/react'
import { useLayoutEffect } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router'
import { EASE } from './components/motion.ts'
import { LookupPage } from './features/lookup/LookupPage.tsx'
import { NotFoundPage } from './features/not-found/NotFoundPage.tsx'
import { ProviderPage } from './features/provider/ProviderPage.tsx'
import { ListStateProvider } from './features/providers/ListStateProvider.tsx'
import { ProvidersPage } from './features/providers/ProvidersPage.tsx'
import { Atmosphere } from './features/shell/Atmosphere.tsx'
import { TabBar } from './features/shell/TabBar.tsx'
import { TopBar } from './features/shell/TopBar.tsx'
import { SourcesPage } from './features/sources/SourcesPage.tsx'

/** Mounted once per page, so each new page starts at the top. */
function ScrollToTop() {
  useLayoutEffect(() => {
    window.scrollTo(0, 0)
  }, [])
  return null
}

/** Pages that share a key do not transition: the add sheet opens over the list. */
function pageKey(pathname: string): string {
  return pathname === '/providers/new' ? '/providers' : pathname
}

export default function App() {
  const location = useLocation()
  const key = pageKey(location.pathname)

  return (
    // reducedMotion="user" makes every animation honour the OS "reduce motion" setting.
    <MotionConfig reducedMotion="user">
      <ListStateProvider>
        <div className="relative isolate min-h-dvh overflow-x-clip">
          <ScrollToTop key={key} />
          <Atmosphere />
          <TopBar />
          <div className="relative min-w-0">
            <AnimatePresence mode="popLayout" initial={false}>
              <motion.div
                key={key}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.26, ease: EASE }}
                className="min-h-dvh w-full"
              >
                <Routes location={location}>
                  <Route path="/" element={<Navigate to="/lookup" replace />} />
                  <Route path="/lookup" element={<LookupPage />} />
                  <Route path="/providers" element={<ProvidersPage />} />
                  <Route path="/providers/new" element={<ProvidersPage />} />
                  <Route path="/providers/:id" element={<ProviderPage />} />
                  <Route path="/sources" element={<SourcesPage />} />
                  <Route path="*" element={<NotFoundPage />} />
                </Routes>
              </motion.div>
            </AnimatePresence>
          </div>
          <TabBar />
        </div>
      </ListStateProvider>
    </MotionConfig>
  )
}
