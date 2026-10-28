import { AnimatePresence, MotionConfig, cancelMicrotask, microtask, motion } from 'motion/react'
import { useLayoutEffect } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router'
import { EASE } from './components/motion.ts'
import { LookupPage } from './features/lookup/LookupPage.tsx'
import { LookupStateProvider } from './features/lookup/LookupStateProvider.tsx'
import { RecordPage } from './features/lookup/RecordPage.tsx'
import { NotFoundPage } from './features/not-found/NotFoundPage.tsx'
import { ProviderPage } from './features/provider/ProviderPage.tsx'
import { ListStateProvider } from './features/providers/ListStateProvider.tsx'
import { ProvidersPage } from './features/providers/ProvidersPage.tsx'
import { Atmosphere } from './features/shell/Atmosphere.tsx'
import { TabBar } from './features/shell/TabBar.tsx'
import { TopBar } from './features/shell/TopBar.tsx'
import { SourcesPage } from './features/sources/SourcesPage.tsx'

/**
 * Mounted once per page, so each new page starts at the top, instantly and before paint.
 *
 * The reset waits for Motion's layout measurement: Motion snapshots layouts before the commit and
 * measures them again in its microtask `read` step, in page coordinates. Scrolling in between
 * makes everything in the floating bars look as if it had moved by the scroll distance, and a
 * shared indicator flew up from below on every route change made while scrolled. Scheduling the
 * reset in `postRender` of the same microtask batch runs it after that measurement and still
 * before the browser paints.
 */
function ScrollToTop() {
  useLayoutEffect(() => {
    const reset = () => window.scrollTo({ top: 0, left: 0, behavior: 'instant' })
    microtask.postRender(reset)
    return () => cancelMicrotask(reset)
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
        <LookupStateProvider>
          <div className="relative isolate min-h-dvh overflow-x-clip">
            <ScrollToTop key={key} />
            <Atmosphere />
            <TopBar />
            <div className="relative min-w-0">
              {/*
               * The rise plays on first load too (a direct load or a refresh), not only on
               * navigation. Motion renders the initial style on the first render, so the first
               * paint shows the page at opacity 0, never a flash of the settled page.
               */}
              <AnimatePresence mode="popLayout">
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
                    <Route path="/lookup/:recordId" element={<RecordPage />} />
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
        </LookupStateProvider>
      </ListStateProvider>
    </MotionConfig>
  )
}
