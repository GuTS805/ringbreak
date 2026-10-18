/** Proxy /api/* to the FastAPI server so one URL serves both the console and the API. */
module.exports = {
  async rewrites() {
    return [{source: '/api/:path*', destination: `${process.env.API_INTERNAL_URL || 'http://127.0.0.1:8000'}/:path*`}];
  },
};
