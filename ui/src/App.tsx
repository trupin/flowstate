import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Sidebar } from './components/Sidebar';
import { FlowLibrary } from './pages/FlowLibrary';
import { RunDetail } from './pages/RunDetail';

export function App() {
  return (
    <BrowserRouter>
      <div style={{ display: 'flex' }}>
        <Sidebar />
        <main
          style={{
            marginLeft: 'var(--sidebar-width)',
            flex: 1,
            minHeight: '100vh',
          }}
        >
          <Routes>
            <Route path="/" element={<FlowLibrary />} />
            <Route path="/runs/:id" element={<RunDetail />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
