import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Sidebar } from './components/Sidebar';

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
            <Route path="/" element={<div>Flow Library (placeholder)</div>} />
            <Route
              path="/runs/:id"
              element={<div>Run Detail (placeholder)</div>}
            />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
