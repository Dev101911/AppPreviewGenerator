# DRAFT Homebrew formula for a tap such as Dev101911/homebrew-tap.
# Finish it on macOS after the first PyPI release; see README.md in this folder.
class Apppreview < Formula
  include Language::Python::Virtualenv

  desc "Generate and validate store-compliant App Store and Google Play screenshots"
  homepage "https://github.com/Dev101911/AppPreviewGenerator"
  url "https://files.pythonhosted.org/packages/source/a/apppreview/apppreview-0.1.0.tar.gz"
  sha256 "REPLACE_WITH_SDIST_SHA256"
  license "MIT"

  depends_on "rust" => :build # pydantic-core builds from source
  depends_on "freetype"
  depends_on "jpeg-turbo"
  depends_on "libraqm" # right-to-left and complex-script text layout
  depends_on "python@3.13"

  # `brew update-python-resources apppreview` fills in resource blocks for
  # click, pillow, pyyaml, pydantic, pydantic-core and their dependencies here.

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "apple.iphone-6.9", shell_output("#{bin}/apppreview targets --store apple")
    system bin/"apppreview", "graphic", "-t", "google.feature-graphic", "--title", "Test", "-o", testpath/"fg.png"
    assert_path_exists testpath/"fg.png"
  end
end
