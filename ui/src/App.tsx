import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Sidebar } from './components/Sidebar';
import { FlowLibrary } from './pages/FlowLibrary';
import { RunDetail } from './pages/RunDetail';
import { TaskDetail } from './pages/TaskDetail';

export function App() {
  return (
    <BrowserRouter>
      <div style={{ display: 'flex' }}>
        <Sidebar />
        <main
          style={{
            marginLeft: 'var(--sidebar-width)',
            width: 'calc(100vw - var(--sidebar-width))',
            maxWidth: 'calc(100vw - var(--sidebar-width))',
            minHeight: '100vh',
            overflow: 'hidden',
          }}
        >
          <Routes>
            <Route path="/" element={<FlowLibrary />} />
            <Route path="/runs/:id" element={<RunDetail />} />
            <Route path="/tasks/:taskId" element={<TaskDetail />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
